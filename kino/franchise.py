"""Франшизы: определение «про что это» и кулдаун между постами о ней.

Зачем. Существующая дедупликация ловит ту же новость (ссылка + сигнатура
заголовка). Она не ловит *разные* новости об одном и том же: «Дюна 3 получила
дату», «Вильнёв о Дюне 3», «кассовые сборы Дюны 2» — три разных материала про
одну франшизу. При 13 слотах в неделю и коротких стыках (пт 23:40 → сб 16:40)
это заметно подписчику.

Правило: не более одного поста про одну франшизу за
`franchise_cooldown_hours` (36). Проверка — на этапе отбора в `choose.py`,
до генерации черновика: отсекать после судьи значит платить токенами за пост,
который не выйдет.

Словарь — `config/franchises.yaml`. Без словаря правило не срабатывает:
ложных срабатываний быть не должно, а угадывать франшизу по заголовку —
источник ошибок.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_HOURS = 36


class FranchiseError(Exception):
    """Ошибка словаря франшиз: мусор в YAML или негодная запись."""


@dataclass(frozen=True)
class Franchise:
    """Франшиза: ключ, человеческое имя и фразы-маркеры."""

    key: str
    title: str
    phrases: tuple[str, ...]

    def __str__(self) -> str:
        return self.title or self.key


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Границы слова для латиницы и кириллицы.

    Две ловушки, обе пойманы тестами:
    1. `[а-яё]` не содержит `ё` (она вне диапазона `а-я`), поэтому «Дюны»
       не находилась вовсе.
    2. Маркер-основа («дюн») с правым lookahead не совпадает никогда:
       после основы всегда стоит окончание, а оно буква. Поэтому основа
       помечается звёздочкой — `дюн*` — и матчится как префикс слова.

    Соглашение: `дюн*` — префикс («Дюна», «Дюны», «Дюне»), `dune` — точное
    слово («Dune», но не «Dunes»). Без звёздочки правая граница обязательна.
    """
    text = phrase.strip().lower()
    prefix = text.endswith("*")
    if prefix:
        text = text[:-1]
    escaped = re.escape(text)
    left = r"(?<![0-9a-zа-яё])"
    if prefix:
        return re.compile(left + escaped, re.IGNORECASE | re.UNICODE)
    return re.compile(left + escaped + r"(?![0-9a-zа-яё])", re.IGNORECASE | re.UNICODE)


def load_franchises(path: Path | str | None) -> list[Franchise]:
    """Читает словарь. Отсутствие файла — пустой список, не ошибка.

    Пустой словарь означает «правило выключено» — это нормальное состояние
    для тестов и для случая, когда владелец ещё не заполнил список.
    """
    if path is None:
        return []
    file = Path(path)
    if not file.exists():
        logger.debug("словарь франшиз не найден: %s — кулдаун выключен", file)
        return []
    try:
        with file.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise FranchiseError(f"ошибка YAML в {file}: {exc}") from exc
    except OSError as exc:
        raise FranchiseError(f"не прочитать {file}: {exc}") from exc

    if not isinstance(data, dict):
        raise FranchiseError(f"{file}: ожидается словарь с ключом franchises")
    raw = data.get("franchises")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise FranchiseError(f"{file}: franchises должен быть списком")

    out: list[Franchise] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise FranchiseError(f"{file}: запись франшизы должна быть словарём: {entry!r}")
        key = str(entry.get("key", "")).strip()
        if not key:
            raise FranchiseError(f"{file}: у франшизы нет key: {entry!r}")
        title = str(entry.get("title", key)).strip()
        # Отсутствие phrases — фолбэк на key. Явный пустой список — ошибка:
        # это опечатка в словаре, а не «использовать key».
        if "phrases" in entry:
            phrases_raw = entry["phrases"]
            if not isinstance(phrases_raw, list):
                raise FranchiseError(
                    f"{file}: franchises.{key}.phrases должен быть списком"
                )
        else:
            phrases_raw = [key]
        phrases = tuple(str(p).strip().lower() for p in phrases_raw if str(p).strip())
        if not phrases:
            raise FranchiseError(f"{file}: franchises.{key}.phrases пуст")
        out.append(Franchise(key=key, title=title, phrases=phrases))
    return out


def detect(text: str, franchises: list[Franchise]) -> list[Franchise]:
    """Все франшизы, упомянутые в тексте. Пустой список — ни одной."""
    if not franchises or not text:
        return []
    lowered = text.lower()
    found: list[Franchise] = []
    for franchise in franchises:
        for phrase in franchise.phrases:
            if _phrase_pattern(phrase).search(lowered):
                found.append(franchise)
                break
    return found


def recent_keys(
    published: list[tuple[str, str]],
    now: datetime,
    cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
) -> set[str]:
    """Ключи франшиз, опубликованных за последние `cooldown_hours`.

    `published` — пары (ключ франшизы, ISO-время публикации). Записи старше
    кулдауна и записи с нечитаемым временем игнорируются: битая метка не
    должна навсегда блокировать франшизу.
    """
    if cooldown_hours <= 0:
        raise FranchiseError(f"cooldown_hours={cooldown_hours}, нужно > 0")
    cutoff = now - timedelta(hours=cooldown_hours)
    active: set[str] = set()
    for key, stamp in published:
        if not key:
            continue
        try:
            moment = datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            logger.warning("франшизы: не разобрать время %r у ключа %s", stamp, key)
            continue
        if moment.tzinfo is None and now.tzinfo is not None:
            moment = moment.replace(tzinfo=now.tzinfo)
        if moment > cutoff:
            active.add(key)
    return active


def filter_cooled_down(
    texts: list[tuple[str, list[Franchise]]],
    active_keys: set[str],
) -> list[bool]:
    """Для каждой записи: можно ли её брать (не все её франшизы в кулдауне).

    Запись отбрасывается, если *любая* её франшиза в кулдауне: пост про
    «Дюну и Дисней» — всё равно пост про Дюну.
    """
    return [not any(f.key in active_keys for f in franchises) for _text, franchises in texts]


def describe_blocked(franchises: list[Franchise], active_keys: set[str]) -> str:
    """Строка для лога: какие франшизы записи попали в кулдаун."""
    hits = [str(f) for f in franchises if f.key in active_keys]
    return ", ".join(hits) if hits else ""


def config_path(raw: Any, root: Path) -> Path | None:
    """Путь к словарю из конфига. Пустое значение — правило выключено."""
    if raw in (None, "", False):
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = root / path
    return path
