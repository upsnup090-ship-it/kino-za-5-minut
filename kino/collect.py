"""Сбор и нормализация RSS/Atom."""

from __future__ import annotations

import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone

import feedparser
import requests

from kino.config import Config, Source

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]*>")
_SPACE_RE = re.compile(r"\s+")


@dataclass
class FeedItem:
    source: str
    title: str
    link: str
    pub_date: str
    ts: float
    snippet: str


@dataclass
class CollectResult:
    items: list[FeedItem] = field(default_factory=list)
    broken: list[str] = field(default_factory=list)
    total: int = 0
    fresh_count: int = 0


def _clean_html(text: str) -> str:
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _SPACE_RE.sub(" ", text)
    return text.strip()


def _extract_snippet(entry: dict, max_length: int) -> str:
    raw = (
        entry.get("contentSnippet")
        or entry.get("description")
        or entry.get("content")
        or entry.get("summary")
        or ""
    )
    if isinstance(raw, list):
        raw = " ".join(
            item.get("value", "") if isinstance(item, dict) else str(item)
            for item in raw
        )
    text = _clean_html(str(raw))
    return text[:max_length]


def _extract_link(entry: dict) -> str:
    link = entry.get("link", "")
    if not link:
        links = entry.get("links", [])
        if links:
            link = links[0].get("href", "")
    return str(link).strip()


def _extract_ts(entry: dict) -> float:
    raw = (
        entry.get("published_parsed")
        or entry.get("updated_parsed")
        or entry.get("created_parsed")
    )
    if raw:
        try:
            return datetime(*raw[:6], tzinfo=timezone.utc).timestamp()
        except (TypeError, ValueError):
            pass
    return 0.0


def _fetch_one(source: Source, cfg: Config) -> tuple[str, list[FeedItem] | None, str | None]:
    try:
        response = requests.get(
            source.url,
            timeout=cfg.settings.collect_timeout,
            headers={"User-Agent": cfg.settings.user_agent},
        )
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        if parsed.bozo and not parsed.entries:
            return source.name, None, f"parse error: {parsed.bozo_exception}"
        if not parsed.entries:
            return source.name, [], None
        items = []
        for entry in parsed.entries:
            title = str(entry.get("title", "")).strip()
            link = _extract_link(entry)
            if not title or not link:
                continue
            items.append(
                FeedItem(
                    source=source.name,
                    title=title,
                    link=link,
                    pub_date=str(entry.get("published", entry.get("updated", ""))),
                    ts=_extract_ts(entry),
                    snippet=_extract_snippet(entry, cfg.settings.snippet_length),
                )
            )
        return source.name, items, None
    except requests.RequestException as exc:
        return source.name, None, f"request error: {exc.__class__.__name__}"
    except Exception as exc:  # noqa: BLE001 — ловим всё, чтобы не уронить сбор
        return source.name, None, f"unexpected error: {exc.__class__.__name__}"


def collect(cfg: Config) -> CollectResult:
    result = CollectResult()
    seen: set[str] = set()

    with ThreadPoolExecutor(max_workers=cfg.settings.max_workers) as executor:
        futures = {
            executor.submit(_fetch_one, source, cfg): source
            for source in cfg.sources
        }
        for future in as_completed(futures):
            source = futures[future]
            name, items, error = future.result()
            if error is not None:
                result.broken.append(f"{name}: {error}")
                logger.warning("источник %s: %s", name, error)
                continue
            if items is None:
                result.broken.append(f"{name}: no data")
                logger.warning("источник %s: нет данных", name)
                continue
            if not items:
                logger.info("источник %s: 0 записей", name)
                continue
            added = 0
            for item in items:
                if item.link in seen:
                    continue
                seen.add(item.link)
                result.items.append(item)
                added += 1
            logger.info("источник %s: %d записей, добавлено %d", name, len(items), added)

    if not result.items:
        broken_list = " | ".join(result.broken) if result.broken else "нет упавших источников"
        raise RuntimeError(f"сбор: 0 записей. broken=[ {broken_list} ]")

    result.total = len(result.items)
    now = datetime.now(timezone.utc).timestamp()
    fresh_limit = cfg.settings.freshness_days * 86400
    fresh = [item for item in result.items if item.ts > 0 and (now - item.ts) < fresh_limit]

    if fresh:
        pool = fresh
    elif cfg.settings.allow_stale_fallback:
        logger.warning("свежих записей нет, берём все (%d)", len(result.items))
        pool = result.items
    else:
        raise RuntimeError("свежих записей нет, а allow_stale_fallback=false")

    result.fresh_count = len(fresh)
    pool.sort(key=lambda item: item.ts, reverse=True)
    result.items = pool[: cfg.settings.top_n]
    return result