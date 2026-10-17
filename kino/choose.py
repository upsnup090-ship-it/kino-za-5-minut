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
}

_MOVIE_TERMS = {
    "рейтинг", "отзыв", "критики", "фильм", "кино", "премьера", "актер",
    "режиссер", "смотреть", "посмотреть", "хочу", "трейлер", "новости кино",
    "рекомендуют", "обзор", "рецензия", "показ", "кинотеатр", "film",
    "movie", "reviews", "rating", "critic",
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
    if len(item.snippet) > 100:
        score += 1
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
    if sad_topic and chosen == "when":
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

    relevant = []
    for item in items:
        text = f"{item.title} {item.snippet} {item.link}"
        if _contains_phrase(text, cfg.bad):
            continue
        if _relevance_score(item) < 1:
            continue
        relevant.append(item)

    usable = relevant if relevant else items

    sigs: list[list[str]] = []
    deduped: list[FeedItem] = []
    for item in usable:
        sig = _signature(item.title)
        if any(_shared(sig, existing) >= 2 for existing in sigs):
            continue
        sigs.append(sig)
        deduped.append(item)

    base_pool = deduped if deduped else usable

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