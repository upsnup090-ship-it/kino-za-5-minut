"""Статьи Telegraph: страница, которая в Telegram открывается как Instant View.

Сеть: только api.telegra.ph (разрешено пользователем).
Токен: TELEGRAPH_TOKEN из окружения; если нет — createAccount и сохранение
токена в data/telegraph_token.json (атомарно).
"""

from __future__ import annotations

import html
import json
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegra.ph"
TIMEOUT = 30


class TelegraphError(Exception):
    """Ошибка Telegraph API."""


def _post(method: str, payload: dict) -> dict:
    try:
        response = requests.post(f"{API_BASE}/{method}", data=payload, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise TelegraphError(f"telegraph {method}: {exc.__class__.__name__}") from exc
    if not data.get("ok"):
        raise TelegraphError(f"telegraph {method}: {data.get('error', 'unknown error')}")
    return data.get("result") or {}


def load_token(state_path: Path) -> str:
    """Токен из TELEGRAPH_TOKEN, иначе из файла, иначе createAccount."""
    token = os.environ.get("TELEGRAPH_TOKEN", "").strip()
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
        raise TelegraphError("createAccount не вернул access_token")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(state_path.parent), text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)
    os.replace(tmp, state_path)
    logger.info("telegraph: создан новый аккаунт, токен сохранён")
    return token


def _text_to_nodes(text: str) -> list[dict]:
    """Текст → узлы Telegraph: абзацы, пустая строка = перенос блока."""
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

    body — текст статьи (HTML-экранирование внутри не нужно, Telegraph
    принимает чистый текст в children).
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
        raise TelegraphError("createPage не вернул url")
    return url
