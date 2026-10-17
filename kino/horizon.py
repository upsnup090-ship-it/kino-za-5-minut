"""Источник Horizon: запуск дайджеста и разбор .md в FeedItem.

Horizon кладёт дневной брифинг в data/summaries/*.md (Markdown, EN или ZH).
Этот модуль превращает такой файл в тот же список FeedItem, что отдаёт
collect.py, поэтому весь остальной пайплайн (choose → draft → judge → image →
caption → publish) работает без изменений.

Запуск Horizon опционален: если uv или сам Horizon недоступны, модуль не
падает, а сообщает об этом внятной ошибкой HorizonError.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from kino.collect import FeedItem

logger = logging.getLogger(__name__)


class HorizonError(Exception):
    """Ошибка источника Horizon."""


# --- разбор Markdown ---------------------------------------------------------

# Заголовок: ## ... или ### ... (уровень 2-3 — секции дайджеста).
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)
# Ссылка в Markdown: [текст](url) или голая http(s)-ссылка.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BARE_URL_RE = re.compile(r"(?<!\()(https?://[^\s<>)\]]+)")
# Строка-источник вида "Source: https://..." или "来源: https://...".
_SOURCE_LINE_RE = re.compile(
    r"^\s*(?:source|источник|来源|출처)\s*[:：]\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# Дата в имени файла: 2026-10-17 или 20261017.
_DATE_IN_NAME_RE = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})")


@dataclass
class HorizonResult:
    """Результат разбора дайджеста — совместим по смыслу с CollectResult."""

    items: list[FeedItem] = field(default_factory=list)
    broken: list[str] = field(default_factory=list)
    total: int = 0
    fresh_count: int = 0
    digest_path: Path | None = None
    lang: str = "unknown"


def _strip_markdown(text: str) -> str:
    """Убирает markdown-разметку, оставляя читаемый текст."""
    text = _MD_LINK_RE.sub(r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    return text.strip()


def _detect_lang(text: str) -> str:
    """Грубо определяет язык дайджеста: zh, en или unknown."""
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    if re.search(r"[A-Za-z]{3,}", text):
        return "en"
    return "unknown"


def _first_url(block: str) -> str:
    """Достаёт первую ссылку из блока: сначала markdown, потом голую."""
    md = _MD_LINK_RE.search(block)
    if md:
        return md.group(2).strip()
    bare = _BARE_URL_RE.search(block)
    if bare:
        return bare.group(1).strip()
    return ""


def _domain(url: str) -> str:
    """Домен без www — короткое имя источника."""
    match = re.match(r"https?://([^/]+)", url)
    if not match:
        return "horizon"
    host = match.group(1).lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _source_name(block: str, fallback: str) -> str:
    """Имя источника: из строки Source:/来源:, иначе — домен ссылки."""
    match = _SOURCE_LINE_RE.search(block)
    if match:
        raw = _strip_markdown(match.group(1))
        url = _first_url(raw)
        if url:
            return _domain(url)
        if raw:
            return raw[:60]
    url = _first_url(block)
    if url:
        return _domain(url)
    return fallback


def _parse_date_from_name(path: Path) -> float:
    """Пытается вытащить дату из имени файла. Нет даты — 0.0."""
    match = _DATE_IN_NAME_RE.search(path.stem)
    if not match:
        return 0.0
    try:
        year, month, day = (int(x) for x in match.groups())
        return datetime(year, month, day, tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


def parse_digest(md: str, source_name: str = "horizon") -> list[FeedItem]:
    """Разбирает Markdown-дайджест в список FeedItem.

    Каждая секция уровня ## или ### считается одной новостью. Заголовок —
    текст секции, ссылка — первая ссылка в блоке, snippet — очищенный текст
    блока без заголовка. Если секций нет, весь файл считается одной новостью.
    """
    text = md.strip()
    if not text:
        return []

    matches = list(_HEADING_RE.finditer(text))
    items: list[FeedItem] = []

    if not matches:
        # Дайджест без секций — одна новость целиком.
        title = _strip_markdown(text.splitlines()[0])[:200]
        body = _strip_markdown(text)
        url = _first_url(text)
        items.append(
            FeedItem(
                source=source_name,
                title=title or "Horizon digest",
                link=url,
                pub_date="",
                ts=0.0,
                snippet=body[:300],
            )
        )
        return items

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        title = _strip_markdown(match.group(2)).strip()
        if not title:
            continue
        url = _first_url(block)
        body = _strip_markdown(block)
        # Убираем строку-источник из тела, чтобы не дублировать её в snippet.
        body = _SOURCE_LINE_RE.sub("", body).strip()
        items.append(
            FeedItem(
                source=_source_name(block, source_name),
                title=title[:200],
                link=url,
                pub_date="",
                ts=0.0,
                snippet=body[:300],
            )
        )
    return items


# --- выбор файла -------------------------------------------------------------


def latest_digest(summaries_dir: Path, explicit: Path | None = None) -> Path:
    """Свежий .md из каталога дайджестов. Явный путь имеет приоритет."""
    if explicit is not None:
        if not explicit.exists():
            raise HorizonError(f"дайджест не найден: {explicit}")
        return explicit
    if not summaries_dir.exists():
        raise HorizonError(
            f"каталог дайджестов не найден: {summaries_dir}. "
            "Запусти Horizon или укажи HORIZON_SUMMARIES_DIR."
        )
    files = sorted(
        summaries_dir.glob("*.md"),
        key=lambda p: (p.stat().st_mtime, p.name),
    )
    if not files:
        raise HorizonError(
            f"в {summaries_dir} нет .md-файлов. Сначала запусти Horizon."
        )
    return files[-1]


# --- запуск Horizon ----------------------------------------------------------


def run_horizon(horizon_dir: Path, hours: int, timeout: float) -> None:
    """Запускает `uv run horizon --hours N` в каталоге Horizon.

    Без shell=True. Если uv не установлен или каталог пуст — HorizonError.
    """
    if not horizon_dir.exists():
        raise HorizonError(
            f"каталог Horizon не найден: {horizon_dir}. "
            "Укажи HORIZON_DIR или запусти с --no-run."
        )
    uv = shutil.which("uv")
    if not uv:
        raise HorizonError(
            "uv не найден в PATH. Установи uv или запусти с --no-run, "
            "положив готовый дайджест в data/summaries."
        )
    cmd = [uv, "run", "horizon", "--hours", str(hours)]
    logger.info("запуск Horizon: %s (cwd=%s)", " ".join(cmd), horizon_dir)
    try:
        subprocess.run(
            cmd,
            cwd=str(horizon_dir),
            check=True,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise HorizonError(f"Horizon не завершился за {timeout:.0f}с") from exc
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or exc.stdout or "").strip().splitlines()[-5:]
        detail = " | ".join(tail) if tail else "без вывода"
        raise HorizonError(
            f"Horizon завершился с кодом {exc.returncode}: {detail}"
        ) from exc


# --- публичная точка входа ---------------------------------------------------


def collect_horizon(
    horizon_dir: Path,
    summaries_dir: Path,
    *,
    run: bool = True,
    hours: int = 24,
    timeout: float = 600.0,
    explicit_file: Path | None = None,
) -> HorizonResult:
    """Полный цикл источника Horizon: (опц.) запуск → выбор файла → разбор."""
    if run and explicit_file is None:
        run_horizon(horizon_dir, hours, timeout)

    path = latest_digest(summaries_dir, explicit_file)
    try:
        md = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HorizonError(f"не удалось прочитать {path}: {exc}") from exc

    lang = _detect_lang(md)
    items = parse_digest(md, source_name=f"horizon/{lang}")

    result = HorizonResult(
        items=items,
        total=len(items),
        fresh_count=len(items),
        digest_path=path,
        lang=lang,
    )
    if not items:
        result.broken.append(f"{path.name}: не удалось разобрать ни одной новости")
        raise HorizonError(
            f"дайджест {path.name} разобран в 0 новостей. "
            "Проверь формат файла или укажи его через --file."
        )

    # Дата из имени файла — как ts, чтобы choose.py сортировал по свежести.
    ts = _parse_date_from_name(path)
    if ts:
        for item in result.items:
            item.ts = ts

    logger.info(
        "Horizon: %s (%s), новостей: %d", path.name, lang, len(result.items)
    )
    return result