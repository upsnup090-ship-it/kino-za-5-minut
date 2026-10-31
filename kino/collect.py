"""Сбор и нормализация RSS/Atom."""

from __future__ import annotations

import html
import hashlib
import logging
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone

from kino.deadline import Deadline
from pathlib import Path

import feedparser
import requests

from kino.config import Config, Source

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]*>")
_SPACE_RE = re.compile(r"\s+")

# Reddit режет запросы по IP: окно видно в x-ratelimit-reset (секунды) и
# держится около минуты. Пять фидов в один прогон в окно не влезают, поэтому
# фиды идут последовательно с паузой, а ответ кэшируется между прогонами —
# при 429 берём последний удачный кэш вместо того, чтобы терять источник.
_REDDIT_HOSTS = ("reddit.com",)

#: Сколько секунд ждать максимум на одном повторе 429 (окно Reddit бывает ~60с,
#: но прогон под cron не должен виснуть на одном источнике).
_REDDIT_MAX_WAIT = 20.0


def _is_reddit(source: Source) -> bool:
    """Источник на Reddit — для него отдельная очередь, пауза и кэш."""
    host = source.url.split("/")[2].lower() if "//" in source.url else ""
    return any(host == h or host.endswith("." + h) for h in _REDDIT_HOSTS)


def _hash_item(item: FeedItem) -> str:
    """Хэширует FeedItem для кэширования и дедупликации."""
    # Используем title + snippet + link для уникальности
    key = f"{item.title}{item.snippet}{item.link}"
    return hashlib.sha256(key.encode()).hexdigest()


def _cache_path(cfg: Config, name: str) -> Path:
    """Путь кэша фида. Имя источника приводим к безопасному виду."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower() or "feed"
    return cfg.cache_dir / f"{slug}.xml"


def _read_cache(cfg: Config, name: str) -> bytes | None:
    """Последний удачный ответ фида. Нет файла — None."""
    path = _cache_path(cfg, name)
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data or None


def _write_cache(cfg: Config, name: str, payload: bytes) -> None:
    """Пишет кэш атомарно. Ошибка записи не роняет сбор."""
    path = _cache_path(cfg, name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".feed-", suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("не удалось сохранить кэш фида %s: %s", name, exc)


@dataclass
class FeedItem:
    source: str
    title: str
    link: str
    pub_date: str
    ts: float
    snippet: str
    #: Картинки и видео из самого фида (media:content / enclosure / embed).
    #: Источник надёжнее Википедии: он даёт кадр именно этой статьи, и
    #: не нужен, когда LLM ссылку на видео выдумал. Пустые значения —
    #: фолбэк на старые пути (wiki-поиск, ссылка новости).
    images: list[str] = field(default_factory=list)
    video_url: str = ""


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


_IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif|avif)(\?|$)", re.IGNORECASE)
# YouTube в разных ипостасях: embed-iframe, короткая ссылка, watch. Из них
# получаем и картинку-постер, и ссылку на видео — фиды несут их прямо.
_YOUTUBE_ID_RE = re.compile(
    r"(?:youtube\.com/(?:embed/|watch\?(?:.*&)?v=)|youtu\.be/)([A-Za-z0-9_-]{6,})"
)


def _media_urls(entry: dict) -> list[str]:
    """Прямые URL картинок из media:content / media:thumbnail / enclosure."""
    urls: list[str] = []
    for group_key in ("media_thumbnail", "media_content", "enclosures"):
        for part in entry.get(group_key) or []:
            if not isinstance(part, dict):
                continue
            url = str(part.get("url") or part.get("href") or "").strip()
            if not url.startswith("http"):
                continue
            medium = str(part.get("medium") or "").lower()
            mime = str(part.get("type") or "").lower()
            is_image = (
                mime.startswith("image")
                or medium == "image"
                or bool(_IMAGE_EXT_RE.search(url))
                or (not mime and not medium)
            )
            if is_image and url not in urls:
                urls.append(url)
    return urls[:4]


def _video_from_entry(entry: dict) -> tuple[str, str]:
    """(video_url, poster_url) из media-групп или YouTube-ссылки в тексте.

    Бот сам ничего не скачивает: video_url идёт в пост-ссылку (Telegram сам
    рисует превью и играет ролик), а postер — последним запасным кадром в
    images, когда фид не отдал своих картинок. iframe'ы feedparser вырезает из
    описания, поэтому ловим только ссылки текстом (watch?v= / youtu.be / embed).
    """
    raw = str(entry.get("media_description") or entry.get("description") or "")
    if isinstance(entry.get("content"), list):
        raw += " " + " ".join(
            item.get("value", "") if isinstance(item, dict) else str(item)
            for item in entry.get("content") or []
        )
    # media:content с medium=video — прямая ссылка на ролик.
    for part in entry.get("media_content") or []:
        if not isinstance(part, dict):
            continue
        medium = str(part.get("medium") or "").lower()
        mime = str(part.get("type") or "").lower()
        url = str(part.get("url") or "").strip()
        if url.startswith("http") and (medium == "video" or mime.startswith("video")):
            return url, ""
    match = _YOUTUBE_ID_RE.search(raw)
    if match:
        vid = match.group(1)
        return (
            f"https://www.youtube.com/watch?v={vid}",
            f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        )
    return "", ""


def _extract_ts(entry: dict) -> float:
    raw = (
        entry.get("published_parsed")
        or entry.get("updated_parsed")
        or entry.get("created_parsed")
    )
    if raw:
        try:
            # feedparser отдаёт time.struct_time (9 полей, без tzinfo), поэтому
            # первые шесть — год..секунда, а пояс подставляем сами. Разбираем
            # по позициям, а не через *raw: иначе проверка типов считает, что
            # tzinfo может прийти из самого кортежа.
            year, month, day, hour, minute, second = tuple(raw[:6])
            return datetime(
                year, month, day, hour, minute, second, tzinfo=timezone.utc
            ).timestamp()
        except (TypeError, ValueError):
            pass
    return 0.0


def _fetch_raw(source: Source, cfg: Config, *, deadline: Deadline | None = None) -> tuple[str, list[FeedItem] | None, str | None]:
    """Один запрос к источнику, без повторов. Ретраи — в _fetch_one."""
    try:
        timeout = cfg.settings.collect_timeout
        if deadline is not None:
            timeout = deadline.slice(timeout)
        response = requests.get(
            source.url,
            timeout=timeout,
            headers={"User-Agent": cfg.settings.user_agent},
        )
        response.raise_for_status()
        _write_cache(cfg, source.name, response.content)
        return _parse_feed(source, response.content, cfg)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        if status == 429 and exc.response is not None:
            # Окно лимита: сколько ждать, знает только заголовок.
            reset = exc.response.headers.get("x-ratelimit-reset")
            return source.name, None, f"http error: 429 (reset={reset or '?'})"
        return source.name, None, f"http error: {status}"
    except requests.RequestException as exc:
        return source.name, None, f"request error: {exc.__class__.__name__}"
    except Exception as exc:  # noqa: BLE001 — ловим всё, чтобы не уронить сбор
        return source.name, None, f"unexpected error: {exc.__class__.__name__}"


def _parse_feed(source: Source, payload: bytes, cfg: Config) -> tuple[str, list[FeedItem] | None, str | None]:
    """Разбирает тело фида в FeedItem. Общий путь для сети и кэша."""
    parsed = feedparser.parse(payload)
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
        video_url, poster_url = _video_from_entry(entry)
        # Картинка поста: сначала кадры из фида, постер YouTube — последним
        # запасным кадром (Telegram скачивает i.ytimg.com по URL, бот к нему
        # не ходит). При 4 чужих картинках постеру места не остаётся.
        images = _media_urls(entry)
        if poster_url and poster_url not in images:
            images = (images + [poster_url])[:4]
        items.append(
            FeedItem(
                source=source.name,
                title=title,
                link=link,
                pub_date=str(entry.get("published", entry.get("updated", ""))),
                ts=_extract_ts(entry),
                snippet=_extract_snippet(entry, cfg.settings.snippet_length),
                images=images,
                video_url=video_url,
            )
        )
    return source.name, items, None


def _fetch_cached(
    source: Source,
    cfg: Config,
    *,
    deadline: Deadline | None = None,
) -> tuple[str, list[FeedItem] | None, str | None]:
    """Сеть, а при 429 — последний удачный ответ из кэша.

    Окно лимита Reddit держится около минуты, и все пять фидов в один прогон
    не влезают. Без кэша четыре источника терялись бы на каждом запуске;
    с кэшем они отдают вчерашний (или утренний) снимок, а свежий подтянется
    в следующий прогон.
    """
    name, items, error = _fetch_one(source, cfg, deadline=deadline)
    if error is None:
        return name, items, error
    if error.startswith("http error: 429"):
        cached = _read_cache(cfg, source.name)
        if cached is not None:
            cname, citems, cerr = _parse_feed(source, cached, cfg)
            if cerr is None and citems:
                logger.info("источник %s: 429, берём кэш (%d записей)", source.name, len(citems))
                return cname, citems, None
    return name, items, error


def _fetch_one(
    source: Source,
    cfg: Config,
    *,
    deadline: Deadline | None = None,
) -> tuple[str, list[FeedItem] | None, str | None]:
    """Запрос к источнику с повторами при сетевых ошибках и лимитах.

    Число попыток и пауза берутся из конфига (retry.max_attempts /
    retry.backoff_seconds). 429 повторяется тоже: у Reddit окно лимита
    закрывается само через x-ratelimit-reset секунд, и без повтора четыре
    фида из пяти терялись на каждом прогоне.

    Дедлайн ограничивает и таймаут запроса, и паузу между повторами:
    если бюджет на исходе, лишняя попытка не запускается.
    """
    attempts = max(1, cfg.settings.retry_max_attempts)
    if _is_reddit(source):
        # Reddit под IP-окном лимита: пять долгих попыток не спасают фид,
        # но выедают минуты у LLM-фазы — для него укороченная серия.
        attempts = min(attempts, max(1, cfg.settings.reddit_max_attempts))
    delay = cfg.settings.retry_backoff
    last: tuple[str, list[FeedItem] | None, str | None] = (source.name, None, "unknown")
    for attempt in range(attempts):
        if deadline is not None and deadline.expired:
            logger.info("источник %s: дедлайн исчерпан, останавливаемся", source.name)
            break
        last = _fetch_raw(source, cfg, deadline=deadline)
        error = last[2]
        if error is None:
            return last
        # HTTP-статус, кроме 429, и ошибки парсинга сами не пройдут.
        retriable = error.startswith("request error") or error.startswith("http error: 429")
        if not retriable:
            return last
        if attempt < attempts - 1:
            wait = delay * (attempt + 1)
            if "429" in error:
                # Уважаем окно лимита, но не спим дольше разумного: reset у
                # Reddit бывает до минуты, а прогон под cron не должен виснуть.
                wait = max(wait, min(_retry_after_from_error(error, delay), _REDDIT_MAX_WAIT))
            if deadline is not None:
                wait = min(wait, deadline.remaining)
            if wait <= 0:
                logger.info("источник %s: дедлайн исчерпан на паузе, выходим", source.name)
                break
            logger.info(
                "источник %s: повтор через %.0fс (%d/%d)",
                source.name, wait, attempt + 1, attempts,
            )
            time.sleep(wait)
    return last


def _retry_after_from_error(error: str, fallback: float) -> float:
    """Секунды из `reset=N` в тексте ошибки. Нет числа — fallback."""
    match = re.search(r"reset=(\d+)", error)
    if not match:
        return fallback
    try:
        return float(match.group(1))
    except ValueError:
        return fallback


def _collect_reddit(
    sources: list[Source],
    cfg: Config,
    *,
    deadline: Deadline | None = None,
) -> list[tuple[str, list[FeedItem] | None, str | None]]:
    """Reddit-фиды последовательно с паузой: окно лимита одно на IP.

    Параллельный заход выбирает окно за один залп, после чего все фиды
    получают 429. Последовательный проход с паузой между запросами
    укладывается в лимит. Дедлайн ограничивает паузу и обрывает проход,
    если бюджет на исходе.
    """
    results: list[tuple[str, list[FeedItem] | None, str | None]] = []
    for index, source in enumerate(sources):
        if deadline is not None and deadline.expired:
            logger.info("Reddit: дедлайн исчерпан, остальные фиды пропускаем")
            break
        if index:
            # Пауза между фидами: окно лимита одно на IP, залп выбирает его
            # целиком. 3с хватает, чтобы пять фидов прошли без 429.
            wait = cfg.settings.reddit_pause
            if deadline is not None:
                wait = min(wait, deadline.remaining)
                if wait <= 0:
                    logger.info("Reddit: дедлайн исчерпан на паузе, выходим")
                    break
            if wait > 0:
                time.sleep(wait)
        results.append(_fetch_cached(source, cfg, deadline=deadline))
    return results


def collect(cfg: Config, *, deadline: Deadline | None = None) -> CollectResult:
    result = CollectResult()
    seen: set[str] = set()
    item_hashes: set[str] = set()  # Для дедупликации по хэшам

    reddit_sources = [s for s in cfg.sources if _is_reddit(s)]
    other_sources = [s for s in cfg.sources if not _is_reddit(s)]

    fetched: list[tuple[str, list[FeedItem] | None, str | None]] = []
    with ThreadPoolExecutor(max_workers=cfg.settings.max_workers) as executor:
        futures = {
            executor.submit(_fetch_cached, source, cfg, deadline=deadline): source
            for source in other_sources
        }
        for future in as_completed(futures):
            fetched.append(future.result())

    if reddit_sources:
        logger.info(
            "Reddit: %d фидов, последовательно с паузой %.1fс (окно лимита на IP)",
            len(reddit_sources), cfg.settings.reddit_pause,
        )
        fetched.extend(_collect_reddit(reddit_sources, cfg, deadline=deadline))

    for name, items, error in fetched:
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
            # Проверяем дубли по ссылке и хэшу
            if item.link in seen:
                continue
            item_hash = _hash_item(item)
            if item_hash in item_hashes:
                continue
                
            seen.add(item.link)
            item_hashes.add(item_hash)
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

    # Записи без даты (часть Reddit-лент) в fresh не попадают никогда: их ts=0.
    # Без этой строки «свежих нет» выглядело как проблема источников, хотя
    # дело в отсутствии даты в фиде.
    undated = sum(1 for item in result.items if item.ts <= 0)
    if undated:
        logger.warning("записей без даты: %d из %d — в свежие не попадут", undated, len(result.items))

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