"""Выбор темы и формата."""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass

from kino.collect import FeedItem
from kino.config import Config

logger = logging.getLogger(__name__)

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


def choose(
    items: list[FeedItem],
    cfg: Config,
    used_links: set[str],
    used_sigs: list[list[str]],
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
    window = candidates[: min(cfg.settings.window_size, len(candidates))]
    item = random.choice(window)

    sad_topic = _contains_phrase(f"{item.title} {item.snippet} {item.link}", cfg.sad)
    fmt = _pick_format(cfg.weights, sad_topic)

    return Choice(
        item=item,
        format=fmt,
        quality_score=_relevance_score(item),
        sad_topic=sad_topic,
    )