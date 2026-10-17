"""Статьи для Telegram: страница, которая открывается как Instant View.

Сервис — graph.org (api.graph.org). Это тот же протокол, что у telegra.ph:
createAccount / createPage, узлы Node в JSON, публичная страница отдаёт
Instant View в Telegram. Домен другой — api.telegra.ph из этой сети
недоступен (ConnectTimeout), api.graph.org отвечает штатно.

Токен: GRAPH_TOKEN из окружения; если нет — createAccount и сохранение
токена в data/graph_token.txt (атомарно).
"""

from __future__ import annotations

import html
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.graph.org"
TIMEOUT = 30
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2.0


def _proxy_config() -> dict | None:
    """Прокси для graph.org: GRAPH_PROXY_URL, иначе LLM_PROXY_URL/PROXY_URL.

    api.graph.org, как и api.telegra.ph, напрямую из этой сети не отвечает
    (ConnectTimeout), через прокси работает штатно.
    """
    url = (
        os.environ.get("GRAPH_PROXY_URL", "").strip()
        or os.environ.get("TELEGRAPH_PROXY_URL", "").strip()
        or os.environ.get("LLM_PROXY_URL", "").strip()
        or os.environ.get("PROXY_URL", "").strip()
    )
    if not url:
        return None
    return {"http": url, "https": url}


class ArticleError(Exception):
    """Ошибка сервиса статей."""


def _post(method: str, payload: dict) -> dict:
    """POST к API статей с повторами.

    Прокси отвечает через раз (ConnectTimeout/ReadTimeout примерно на каждом
    третьем запросе), поэтому сетевые сбои повторяем — иначе статья теряется
    из-за одного неудачного соединения. HTTP-ошибки API не повторяем.
    """
    url = f"{API_BASE}/{method}"
    proxies = _proxy_config()
    last_error: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            response = requests.post(url, data=payload, timeout=TIMEOUT, proxies=proxies)
            response.raise_for_status()
            data = response.json()
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt < RETRY_ATTEMPTS - 1:
                delay = RETRY_BACKOFF * (attempt + 1)
                logger.warning(
                    "article %s: %s, повтор через %.1fс (%d/%d)",
                    method, exc.__class__.__name__, delay, attempt + 1, RETRY_ATTEMPTS,
                )
                time.sleep(delay)
            continue
        except (requests.RequestException, ValueError) as exc:
            raise ArticleError(f"article {method}: {exc.__class__.__name__}") from exc

        if not data.get("ok"):
            raise ArticleError(f"article {method}: {data.get('error', 'unknown error')}")
        return data.get("result") or {}

    raise ArticleError(f"article {method}: {last_error.__class__.__name__}") from last_error


def load_token(state_path: Path) -> str:
    """Токен из GRAPH_TOKEN, иначе из файла, иначе createAccount."""
    token = os.environ.get("GRAPH_TOKEN", "").strip()
    if token:
        return token
    try:
        token = state_path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if token:
        return token
    result = _post(
        "createAccount",
        {
            "short_name": "KinoZa5Minut",
            "author_name": "Кино за 5 минут",
        },
    )
    token = str(result.get("access_token", ""))
    if not token:
        raise ArticleError("createAccount не вернул access_token")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(state_path.parent), text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)
    os.replace(tmp, state_path)
    logger.info("article: создан новый аккаунт, токен сохранён")
    return token


def _text_to_nodes(text: str) -> list[dict]:
    """Текст → узлы статьи: абзацы, пустая строка = перенос блока."""
    nodes: list[dict] = []
    for block in html.unescape(text).split("\n"):
        block = block.strip()
        if not block:
            continue
        nodes.append({"tag": "p", "children": [block]})
    return nodes or [{"tag": "p", "children": [""]}]


def create_page(
    token: str,
    title: str,
    body: str,
    image_url: str = "",
    source_url: str = "",
) -> str:
    """Создаёт страницу, возвращает её URL.

    body — текст статьи (HTML-экранирование внутри не нужно, сервис принимает
    чистый текст в children).
    """
    nodes: list[dict] = []
    if image_url:
        nodes.append({"tag": "img", "attrs": {"src": image_url}})
    nodes.extend(_text_to_nodes(body))
    if source_url and urlparse(source_url).scheme in ("http", "https"):
        nodes.append(
            {
                "tag": "p",
                "children": ["Источник: "],
            }
        )
        nodes.append(
            {
                "tag": "a",
                "attrs": {"href": source_url},
                "children": [source_url],
            }
        )
    result = _post(
        "createPage",
        {
            "access_token": token,
            "title": title[:256] or "Кино за 5 минут",
            "author_name": "Кино за 5 минут",
            "content": json.dumps(nodes, ensure_ascii=False),
            "return_content": "false",
        },
    )
    url = str(result.get("url", ""))
    if not url:
        raise ArticleError("createPage не вернул url")
    return url