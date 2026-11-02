"""Тонкий клиент Telegram Bot API для диспетчера.

Используется для polling (getUpdates), отправки сообщений и проверки токена (getMe).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"

#: Сколько ждать long polling (секунд). Telegram держит соединение открытым.
LONG_POLL_TIMEOUT = 25


class TgError(Exception):
    """Ошибка Telegram API."""


class TgConflict(TgError):
    """409 Conflict — другой процесс уже читает этим токеном."""


def _api_url(token: str, method: str) -> str:
    return f"{API_BASE}/bot{token}/{method}"


def _request(token: str, method: str, **kwargs: Any) -> dict:
    """Универсальный запрос к Telegram API."""
    url = _api_url(token, method)
    kwargs.setdefault("timeout", 15)
    try:
        resp = requests.post(url, **kwargs) if kwargs else requests.get(url, **kwargs)
    except requests.RequestException as exc:
        raise TgError(f"сетевой сбой: {exc}") from exc

    if resp.status_code == 409:
        raise TgConflict(f"409 Conflict: другой процесс поллит токен ({token[:12]}…)")

    if resp.status_code >= 400:
        raise TgError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    if not data.get("ok"):
        raise TgError(f"API error: {data.get('description', 'неизвестно')}")
    return data["result"]


def get_me(token: str) -> dict:
    """Проверка токена: возвращает информацию о боте."""
    return _request(token, "getMe")


def get_updates(
    token: str, offset: int | None = None, timeout: int = LONG_POLL_TIMEOUT
) -> list[dict]:
    """Получить апдейты (long poll)."""
    params: dict[str, Any] = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    return _request(token, "getUpdates", params=params)


def send_message(
    token: str, chat_id: int | str, text: str, **kwargs: Any
) -> dict:
    """Отправить сообщение."""
    payload = {"chat_id": chat_id, "text": text, **kwargs}
    return _request(token, "sendMessage", json=payload)


def answer_callback(token: str, callback_id: str, text: str | None = None) -> None:
    """Ответить на callback (убрать «часики» на кнопке)."""
    payload: dict[str, Any] = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    _request(token, "answerCallbackQuery", json=payload)


def edit_message(
    token: str, chat_id: int, message_id: int, text: str, **kwargs: Any
) -> dict:
    """Редактировать сообщение."""
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        **kwargs,
    }
    return _request(token, "editMessageText", json=payload)
