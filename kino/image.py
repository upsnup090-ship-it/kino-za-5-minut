"""Картинка и карточка из Wikipedia."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import requests

from kino.config import Config

logger = logging.getLogger(__name__)


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


def _search(lang: str, query: str, cfg: Config) -> dict | None:
    try:
        response = requests.get(
            f"https://{lang}.wikipedia.org/w/rest.php/v1/search/page",
            params={"q": query, "limit": 1},
            headers={"User-Agent": cfg.settings.wiki_user_agent},
            timeout=cfg.settings.wiki_timeout,
        )
        response.raise_for_status()
        pages = response.json().get("pages") or []
        return pages[0] if pages else None
    except (requests.RequestException, ValueError) as exc:
        logger.warning("wiki %s: %s", lang, exc.__class__.__name__)
        return None


def _thumbnail(page: dict) -> str:
    thumb = page.get("thumbnail") or {}
    url = str(thumb.get("url") or thumb.get("source") or "")
    if url.startswith("//"):
        url = "https:" + url
    url = url.replace("//thumb.wikimedia.org/", "//upload.wikimedia.org/")
    if "?" in url:
        url = url.split("?")[0]
    if "no_image" in url:
        return ""
    return url


def find_image(wiki_title: str, cfg: Config) -> ImageResult:
    """Ищет картинку: сначала ru, затем en.

    Поле wiki может прийти как название статьи или как ссылка на википедию —
    нормализуем через _wiki_query."""
    query, hinted_lang = _wiki_query(wiki_title)
    if not query:
        return ImageResult()
    langs = ("ru", "en")
    if hinted_lang in ("ru", "en"):
        langs = (hinted_lang, "en" if hinted_lang == "ru" else "ru")
    for lang in langs:
        page = _search(lang, query, cfg)
        if not page:
            continue
        url = _thumbnail(page)
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
            page = _search(lang, query, cfg)
            if not page:
                continue
            url = _thumbnail(page)
            if url and url not in urls:
                urls.append(url)
    return urls