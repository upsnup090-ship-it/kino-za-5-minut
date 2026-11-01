"""Выбор темы и формата."""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kino.collect import FeedItem
from kino.config import Config
from kino.franchise import (
    FranchiseError,
    config_path,
    describe_blocked,
    detect,
    load_franchises,
    recent_keys,
)

logger = logging.getLogger(__name__)

#: Корень проекта — для относительных путей словаря франшиз.
ROOT = Path(__file__).resolve().parents[1]

_STOP_WORDS = {
    "film", "films", "movie", "movies", "series", "show", "shows", "trailer",
    "teaser", "first", "last", "next", "after", "about", "into", "over",
    "more", "most", "will", "with", "from", "that", "this", "their",
    "there", "here", "what", "when", "which", "star", "stars", "director",
    "directors", "new", "news", "exclusive", "watch", "video", "report",
    "reports", "interview", "update", "reveals", "revealed", "official",
    "part", "says", "said", "talks", "стало", "известно", "фильм",
    "фильма", "фильме", "сериала", "трейлер", "первый", "новая", "новый",
    "который", "информационный", "новостной",
    # Русские рубрики и жанровые маркеры: без них «Премьера Дюны в Москве»
    # и «Премьера Дюны в Лондоне» схлопывались в одну сигнатуру и вторая
    # запись отбрасывалась как дубль.
    "премьера", "премьеры", "премьеру", "сериал", "сериалы", "режиссер",
    "режиссёра", "режиссеры", "актер", "актёр", "актеры", "актёры",
    "смотреть", "посмотреть", "показ", "показа", "прокат", "проката",
    "обзор", "обзора", "рецензия", "рецензии", "новости", "новость",
    "рассказал", "рассказала", "сообщил", "сообщила", "подтвердил",
    "назван", "названа", "названы", "станет", "может", "будет", "этого",
    "этой", "этом", "своих", "своей", "которого", "которая", "которые",
    "также", "более", "самый", "самая", "самые", "главный", "главная",
}

_MOVIE_TERMS = {
    "рейтинг", "отзыв", "критики", "фильм", "кино", "премьера", "актер",
    "режиссер", "смотреть", "посмотреть", "хочу", "трейлер", "новости кино",
    "рекомендуют", "обзор", "рецензия", "показ", "кинотеатр", "film",
    "movie", "reviews", "rating", "critic",
    # Расширение: одного film/movie в заголовке часто нет, а материал киношный.
    # Рецензии, документалки, сериалы, роли, фестивали, студии.
    "review", "reviewed", "documentary", "doc", "series", "season", "episode",
    "trailer", "teaser", "remake", "reboot", "sequel", "prequel", "cast",
    "casting", "actor", "actress", "director", "filmmaker", "screenwriter",
    "studio", "box office", "festival", "premiere", "screening", "debut",
    "role", "character", "franchise", "scene", "cinematography", "oscars",
    "academy", "emmy", "golden globe", "cannes", "venice", "sundance",
    "bafta", "anime", "animation", "screenplay", "plot", "starring",
    "рецензия", "документальный", "документалка", "сериал", "сезон", "эпизод",
    "роль", "персонаж", "кастинг", "сценарий", "студия", "фестиваль",
    "показ", "дебют", "экранизация", "сиквел", "ремейк", "перезапуск",
    "кинопрокат", "прокат", "сборы", "дубляж", "озвучка", "оператор",
    "награда", "премия", "номинация", "анимация", "аниме",
}

_POSITIVE_WORDS = {
    "лучший", "рекомендует", "обожают", "нравится", "супер", "отлично",
    "бесподобно",
}

_NEGATIVE_WORDS = {
    "ужасный", "плохой", "совсем не", "ненавижу", "отстой", "скучный",
}


@dataclass
class Choice:
    item: FeedItem
    format: str
    quality_score: int
    sad_topic: bool


def _word_boundary_pattern(phrase: str) -> re.Pattern:
    escaped = re.escape(phrase)
    return re.compile(rf"(?<![a-zа-яё0-9]){escaped}(?![a-zа-яё0-9])", re.IGNORECASE)


def _contains_phrase(text: str, phrases: list[str]) -> bool:
    lowered = text.lower()
    for phrase in phrases:
        pattern = _word_boundary_pattern(phrase)
        if pattern.search(lowered):
            return True
    return False


def _relevance_score(item: FeedItem) -> int:
    text = f"{item.title} {item.snippet}".lower()
    score = 0
    for term in _MOVIE_TERMS:
        pattern = _word_boundary_pattern(term)
        if pattern.search(text):
            score += 2
    for word in _POSITIVE_WORDS:
        pattern = _word_boundary_pattern(word)
        if pattern.search(text):
            score += 1
    for word in _NEGATIVE_WORDS:
        pattern = _word_boundary_pattern(word)
        if pattern.search(text):
            score -= 2
    # Бонус за длинный snippet намеренно не начисляется: длина текста не
    # признак киношности, а при низком пороге он вытягивал в выдачу мусор.
    return score


def _signature(title: str) -> list[str]:
    text = re.sub(r"[^a-zа-яё0-9]+", " ", title.lower())
    parts = text.split()
    out: list[str] = []
    for part in parts:
        if len(part) > 3 and part not in _STOP_WORDS and part not in out:
            out.append(part)
    return out


def _shared(a: list[str], b: list[str]) -> int:
    return len(set(a) & set(b))


#: Форматы, уместные на траурных/криминальных темах (cfg.sad). Остальные
#: заменяются на news: опрос «Какой фильм с погибшим актёром лучший?»,
#: загадка про смерть или сравнение кассовых провалов после трагедии —
#: репутационный провал канала, а не контент.
_SAD_SAFE = {"news", "facts", "quote"}


def _apply_franchise_cooldown(
    items: list[FeedItem],
    cfg: Config,
    franchise_history: list[tuple[str, str]] | None,
    now: datetime | None,
) -> list[FeedItem]:
    """Убирает записи, чья франшиза попала в кулдаун.

    Возвращает исходный список, если правило выключено (нет словаря, нулевой
    кулдаун) или если после фильтра не осталось ни одной записи: пустой
    результат здесь означал бы «прогон без темы», а честнее отдать исходный
    набор — сетка не должна рваться из-за кулдауна. Отсев виден в логе.
    """
    if not franchise_history:
        return items
    cooldown = cfg.settings.franchise_cooldown_hours
    if cooldown <= 0:
        return items

    try:
        franchises = load_franchises(config_path(cfg.settings.franchises_path, ROOT))
    except FranchiseError as exc:
        # Битая словарная запись не должна ронять публикацию: правило
        # выключается, причина — в лог.
        logger.warning("франшизы: словарь не прочитан (%s), кулдаун выключен", exc)
        return items
    if not franchises:
        return items

    moment = now or datetime.now().astimezone()
    active = recent_keys(franchise_history, moment, cooldown)
    if not active:
        return items

    kept: list[FeedItem] = []
    blocked = 0
    for item in items:
        found = detect(f"{item.title} {item.snippet}", franchises)
        if any(f.key in active for f in found):
            blocked += 1
            logger.debug("франшиза в кулдауне (%s): %s",
                         describe_blocked(found, active), item.title[:70])
            continue
        kept.append(item)

    if blocked:
        logger.info("франшизный кулдаун: отсеяно %d записей (активны: %s)",
                    blocked, ", ".join(sorted(active)))
    return kept if kept else items


def _routing_enabled(cfg: Config) -> bool:
    """Включена ли маршрутизация. Откат — одна строка: `routing.enabled: false`.

    `cfg.routing` — это `formats.yaml: routing` целиком, включая служебные
    ключи `enabled` и `fallback`. По умолчанию (секции нет) — включено:
    `load_config` подставляет `DEFAULT_ROUTING`, где `enabled` отсутствует,
    а отсутствие значит True.
    """
    return bool((cfg.routing or {}).get("enabled", True))


def _pick_format(weights: dict[str, int], sad_topic: bool) -> str:
    total = sum(weights.values())
    if total <= 0:
        return "facts"
    roll = random.random() * total
    chosen = "facts"
    for fmt, weight in weights.items():
        roll -= weight
        if roll <= 0:
            chosen = fmt
            break
    if sad_topic and chosen not in _SAD_SAFE:
        return "news"
    return chosen


def _pick_format_routed(
    item: FeedItem,
    cfg: Config,
    sad_topic: bool,
) -> str:
    """Формат по маршрутизации: классы материала → допущенные → взвешенный выбор.

    Подключение Фазы 4 (docs/source-routing-design.md). Три слоя, все
    детерминированные и без LLM:

    1. `detect_kinds` — классы материала (априор фида из `cfg.sources` +
       маркеры в заголовке/сниппете);
    2. `detect_signals` — дата/числа/спикер/видео;
    3. `allowed_formats` → `pick_format` — взвешенный рандол внутри
       допущенных (веса ренормализуются).

    Sad-фильтр применяется **после** маршрута, как и раньше: траурная тема
    не должна получить `poll`/`riddle`, даже если маршрут их допустил.
    Фолбэк — `news` (как в `DEFAULT_ROUTING["fallback"]`).
    """
    from kino.routing import allowed_formats, detect_kinds, detect_signals, pick_format

    feed_kind = _feed_kind_for(item, cfg)
    kinds = detect_kinds(item, feed_kind)
    signals = detect_signals(item)
    allowed = allowed_formats(kinds, signals, cfg.routing or None)
    chosen = pick_format(cfg.weights, allowed)

    logger.info(
        "маршрут: kind=%s сигналы=%s допущено=%s выбран=%s",
        ",".join(sorted(kinds)),
        ",".join(sorted(signals)) or "нет",
        ",".join(allowed),
        chosen,
    )

    if sad_topic and chosen not in _SAD_SAFE:
        logger.info("маршрут: sad-фильтр заменил %s на news", chosen)
        return "news"
    return chosen


def _feed_kind_for(item: FeedItem, cfg: Config) -> str:
    """Априорный класс фида по имени источника из `cfg.sources`.

    `FeedItem.source` — это `Source.name` (заполняется в `collect`), а kind
    живёт только в `cfg.sources`. Неизвестный источник (например, horizon)
    — `mixed`: классификатор элемента решает сам.
    """
    for source in cfg.sources:
        if source.name == item.source:
            return source.kind
    return "mixed"


def choose(
    items: list[FeedItem],
    cfg: Config,
    used_links: set[str],
    used_sigs: list[list[str]],
    *,
    franchise_history: list[tuple[str, str]] | None = None,
    now: datetime | None = None,
) -> Choice:
    if not items:
        raise RuntimeError("выбор: нет записей")

    # bad-фильтр применяем первым и отдельно: фолбэк ниже не должен
    # воскрешать записи, которые явно запрещены правилами канала.
    allowed = [
        item for item in items
        if not _contains_phrase(f"{item.title} {item.snippet} {item.link}", cfg.bad)
    ]
    if not allowed:
        # Все записи запрещены правилами — лучше пропустить прогон
        # (админ получит уведомление), чем опубликовать не-киношный мусор.
        raise RuntimeError("выбор: все записи отфильтрованы как нерелевантные")

    relevant = []
    for item in allowed:
        # Порог 1: одно уверенное киношное слово уже делает материал годным.
        # Порог 2 резал почти всю выдачу (рецензии и документалки без слова
        # film/movie в заголовке), из-за чего пайплайн уходил в фолбэк и
        # публиковал дубль. Спортивные SEO-заметки ловит bad-фильтр выше.
        if _relevance_score(item) < 1:
            continue
        relevant.append(item)

    usable = relevant if relevant else allowed

    sigs: list[list[str]] = []
    deduped: list[FeedItem] = []
    for item in usable:
        sig = _signature(item.title)
        if any(_shared(sig, existing) >= 2 for existing in sigs):
            continue
        sigs.append(sig)
        deduped.append(item)

    base_pool = deduped if deduped else usable

    # Дедупликация по ссылке и сигнатуре. Фильтр по хэшу здесь был мёртвым
    # кодом: used_hashes строился только из элементов, уже отсеянных
    # проверкой `item.link not in used_links`, поэтому вторая проверка
    # никогда не срабатывала. Хэширование нужно в collect() — там оно
    # отсеивает дубли внутри одного прогона.
    fresh = [
        item for item in base_pool
        if item.link not in used_links
        and not any(_shared(_signature(item.title), sig) >= 2 for sig in used_sigs)
    ]
    candidates = fresh if fresh else base_pool

    # Франшизный кулдаун. Дополнение к дедупликации выше, а не замена: та
    # ловит ту же новость, эта — разные новости про одну франшизу. Проверка
    # здесь, до генерации черновика: отсекать после судьи значит платить
    # токенами за пост, который не выйдет. Пустой словарь или выключенный
    # кулдаун — фильтр не применяется вовсе (ложных срабатываний не бывает
    # по построению).
    candidates = _apply_franchise_cooldown(candidates, cfg, franchise_history, now)

    window = candidates[: min(cfg.settings.window_size, len(candidates))]
    item = random.choice(window)

    sad_topic = _contains_phrase(f"{item.title} {item.snippet} {item.link}", cfg.sad)
    if _routing_enabled(cfg):
        fmt = _pick_format_routed(item, cfg, sad_topic)
    else:
        fmt = _pick_format(cfg.weights, sad_topic)

    return Choice(
        item=item,
        format=fmt,
        quality_score=_relevance_score(item),
        sad_topic=sad_topic,
    )