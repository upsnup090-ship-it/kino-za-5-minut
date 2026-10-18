"""Картинка и карточка из Wikipedia.

Два эндпоинта, и это принципиально:

- `w/rest.php/v1/search/page` — поиск статьи по названию. Он отдаёт
  `thumbnail` не всегда: у свежих фильмов (например, «Whalefall» 2026)
  в результатах поиска превью пустое, хотя статья существует.
- `api/rest_v1/page/summary/<key>` — превью по точному ключу статьи. Здесь
  картинка есть всегда, когда она есть у статьи.

Поэтому поиск даёт кандидатов, а картинку берём из summary. Плюс отбор:
при `limit=1` первым результатом часто идёт актёр или роман, а не фильм,
так что берём несколько кандидатов и предпочитаем тот, чьё описание
говорит о фильме.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlparse

import requests

from kino.config import Config

logger = logging.getLogger(__name__)

# Минимальная пауза между запросами к Wikipedia (секунды).
_WIKI_MIN_INTERVAL = 0.8
_last_request = 0.0

# Признак произведения в описании статьи: фильм, сериал, передача, ток-шоу.
# Без «передача»/«ток-шоу» запрос вида «Saturday Night Live UK» уходил на
# статью актёра — в пост попадала фотография случайной персоны.
_FILM_HINT_RE = re.compile(
    r"\b(фильм|кинофильм|мультфильм|телесериал|сериал|телепередача|передача|"
    r"ток-шоу|телешоу|шоу|мультсериал|франшиза|"
    r"film|movie|series|television|sitcom|show)\b",
    re.IGNORECASE,
)


@dataclass
class ImageResult:
    url: str = ""
    title: str = ""
    lang: str = "none"


def _wiki_query(raw: str) -> tuple[str, str]:
    """Приводит поле wiki к запросу поиска.

    Модель возвращает его по-разному: название статьи («Братья Коэн») или
    полную ссылку («https://ru.wikipedia.org/wiki/Братья_Коэн»). Во втором
    случае берём из URL название статьи и, если он википедийный, язык —
    чтобы не искать «https://...» как строку и не терять картинку.
    """
    text = (raw or "").strip()
    if not text:
        return "", ""
    if text.startswith("http://") or text.startswith("https://"):
        parsed = urlparse(text)
        host = parsed.netloc.lower()
        lang = ""
        if host.endswith(".wikipedia.org"):
            lang = host.split(".")[0]
        path = parsed.path
        if "/wiki/" in path:
            title = unquote(path.split("/wiki/", 1)[1]).replace("_", " ").strip()
        else:
            title = ""
        return title, lang
    return text, ""


def _search(lang: str, query: str, cfg: Config, limit: int = 3) -> list[dict]:
    """Поиск страниц. Возвращает список результатов (может быть пустым).

    Wikipedia отдаёт 429 при частых запросах, поэтому здесь пауза между
    вызовами и повторы с уважением к Retry-After. Без этого пакетный поиск
    картинок (gallery) стабильно упирается в лимит.
    """
    url = f"https://{lang}.wikipedia.org/w/rest.php/v1/search/page"
    for attempt in range(3):
        try:
            response = requests.get(
                url,
                params={"q": query, "limit": limit},
                headers={"User-Agent": cfg.settings.wiki_user_agent},
                timeout=cfg.settings.wiki_timeout,
            )
        except requests.RequestException as exc:
            logger.warning("wiki %s: %s", lang, exc.__class__.__name__)
            return []
        if response.status_code == 429:
            wait = float(response.headers.get("Retry-After") or 5 * (attempt + 1))
            logger.warning("wiki %s: 429, ждём %.0fс (%d/3)", lang, wait, attempt + 1)
            time.sleep(wait)
            continue
        try:
            response.raise_for_status()
            pages = response.json().get("pages") or []
        except (requests.RequestException, ValueError) as exc:
            logger.warning("wiki %s: %s", lang, exc.__class__.__name__)
            return []
        _throttle()
        return [p for p in pages if isinstance(p, dict)]
    return []


def _throttle() -> None:
    """Пауза между запросами к Wikipedia: без неё ловим 429."""
    global _last_request
    elapsed = time.monotonic() - _last_request
    if elapsed < _WIKI_MIN_INTERVAL:
        time.sleep(_WIKI_MIN_INTERVAL - elapsed)
    _last_request = time.monotonic()


def _norm(text: str) -> str:
    """Нормализует строку для сравнения названий."""
    return re.sub(r"[^a-zа-яё0-9]+", " ", text.lower().replace("_", " ")).strip()


def _pick_page(pages: list[dict], query: str) -> dict | None:
    """Выбирает страницу из результатов поиска.

    Wikipedia ранжирует сама, но для составных запросов («Saturday Night Live
    UK») первыми часто идут персоны, а не передача или фильм — тогда в пост
    уходила фотография случайного актёра. Поэтому:

    1. Первая страница — произведение и связана с запросом: берём её
       (для «Братья Коэн» первая — статья о режиссёрах, и это верный ответ).
    2. Иначе ищем среди результатов произведение (фильм/сериал/передача),
       пересекающееся с запросом.
    3. Если произведения нет — страницу с наибольшим пересечением с запросом,
       а не просто первую.
    """
    if not pages:
        return None
    first = pages[0]
    query_words = {w for w in _norm(query).split() if len(w) > 2}
    first_words = set(_norm(first.get("key", "")).split()) | set(
        _norm(first.get("title", "")).split()
    )
    first_haystack = f"{first.get('description', '')} {first.get('key', '')}"
    first_is_work = bool(_FILM_HINT_RE.search(first_haystack))

    if not query_words or (first_is_work and (first_words & query_words)):
        return first

    # Первая страница — персона или не совпала: ищем произведение.
    for page in pages[1:]:
        haystack = f"{page.get('description', '')} {page.get('key', '')}"
        if not _FILM_HINT_RE.search(haystack):
            continue
        words = set(_norm(page.get("key", "")).split()) | set(
            _norm(page.get("title", "")).split()
        )
        if not query_words or (words & query_words):
            return page

    # Произведения нет: берём страницу с наибольшим пересечением с запросом.
    best = first
    best_overlap = len(first_words & query_words)
    for page in pages[1:]:
        words = set(_norm(page.get("key", "")).split()) | set(
            _norm(page.get("title", "")).split()
        )
        overlap = len(words & query_words)
        if overlap > best_overlap:
            best, best_overlap = page, overlap
    return best


def _summary_thumb(lang: str, key: str, cfg: Config) -> str:
    """Превью статьи по точному ключу. Пусто, если картинки нет."""
    _throttle()
    try:
        response = requests.get(
            f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(key, safe='')}",
            headers={"User-Agent": cfg.settings.wiki_user_agent},
            timeout=cfg.settings.wiki_timeout,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("wiki summary %s/%s: %s", lang, key, exc.__class__.__name__)
        return ""
    thumb = data.get("thumbnail") or data.get("originalimage") or {}
    source = str(thumb.get("source") or "")
    return _clean_url(source)


def _clean_url(url: str) -> str:
    """Нормализует URL картинки: протокол, домен, размер, query."""
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    # thumb.wikimedia.org — старый алиас; upload.wikimedia.org работает всегда.
    url = url.replace("//thumb.wikimedia.org/", "//upload.wikimedia.org/")
    # Убираем query, но сохраняем путь со /thumb/.../Npx-: он даёт нужный размер.
    if "?" in url:
        url = url.split("?")[0]
    if "no_image" in url:
        return ""
    return url


def _thumb_from_search(page: dict) -> str:
    """Превью прямо из результата поиска — быстрый путь, когда оно есть."""
    thumb = page.get("thumbnail") or {}
    return _clean_url(str(thumb.get("url") or thumb.get("source") or ""))


def find_image_multi(candidates: list[str], cfg: Config) -> ImageResult:
    """Ищет картинку по нескольким кандидатам по порядку.

    Судья заполняет wiki не всегда, но может заполнить film или director —
    тогда картинку берём по ним, а не уходим в пост без изображения.

    Порядок для каждого кандидата: поиск → отбор страницы-фильма → превью из
    summary (там оно есть даже когда поиск его не отдал).
    """
    for raw in candidates:
        query, hinted_lang = _wiki_query(raw)
        if not query:
            continue
        langs = ("ru", "en")
        if hinted_lang in ("ru", "en"):
            langs = (hinted_lang, "en" if hinted_lang == "ru" else "ru")
        for lang in langs:
            pages = _search(lang, query, cfg)
            page = _pick_page(pages, query)
            if not page:
                continue
            # Быстрый путь: превью уже в результатах поиска.
            url = _thumb_from_search(page)
            if not url:
                # Медленный путь: точный ключ статьи и summary-эндпоинт.
                key = str(page.get("key") or "")
                if key:
                    url = _summary_thumb(lang, key, cfg)
            if url:
                return ImageResult(url=url, title=str(page.get("title", "")), lang=lang)
    return ImageResult()


def find_images(wiki_title: str, extra_titles: list[str], cfg: Config) -> list[str]:
    """До 4 уникальных картинок: основная тема (ru+en) + связанные темы.

    Используется для вариации gallery (карусель).
    """
    urls: list[str] = []
    queries: list[str] = []
    main, _ = _wiki_query(wiki_title)
    if main:
        queries.append(main)
    for extra in extra_titles:
        q, _ = _wiki_query(extra)
        if q:
            queries.append(q)
    for query in queries:
        if len(urls) >= 4:
            break
        for lang in ("ru", "en"):
            if len(urls) >= 4:
                break
            if not query:
                continue
            pages = _search(lang, query, cfg)
            page = _pick_page(pages, query)
            if not page:
                continue
            url = _thumb_from_search(page)
            if not url:
                key = str(page.get("key") or "")
                if key:
                    url = _summary_thumb(lang, key, cfg)
            if url and url not in urls:
                urls.append(url)
    return urls