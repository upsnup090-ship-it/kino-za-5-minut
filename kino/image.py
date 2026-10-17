"""Картинка и карточка из Wikipedia."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from kino.config import Config

logger = logging.getLogger(__name__)


@dataclass
class ImageResult:
    url: str = ""
    title: str = ""
    lang: str = "none"


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
    """Ищет картинку: сначала ru, затем en."""
    if not wiki_title:
        return ImageResult()
    for lang in ("ru", "en"):
        page = _search(lang, wiki_title, cfg)
        if not page:
            continue
        url = _thumbnail(page)
        if url:
            return ImageResult(url=url, title=str(page.get("title", "")), lang=lang)
    return ImageResult()