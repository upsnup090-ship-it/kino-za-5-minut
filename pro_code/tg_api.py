"""Тонкий клиент Telegram Bot API для диспетчера.

Используется для polling (getUpdates), отправки сообщений и проверки токена (getMe).

Отправка медиа: ``send_photo`` / ``send_document`` / ``send_video`` принимают
file_id, URL или локальный путь. Локальный путь уходит multipart-ом (``files=``),
всё остальное — обычным JSON-ом.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"

#: Сколько ждать long polling (секунд). Telegram держит соединение открытым.
LONG_POLL_TIMEOUT = 25

#: Поле в методе Bot API, куда кладётся файл.
_MEDIA_FIELD = {
    "sendPhoto": "photo",
    "sendDocument": "document",
    "sendVideo": "video",
}


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
    """Отправить сообщение. Возвращает объект Message (с message_id).

    ``reply_markup`` — dict или JSON-строка инлайн-клавиатуры.
    """
    payload = {"chat_id": chat_id, "text": text, **kwargs}
    markup = payload.get("reply_markup")
    if isinstance(markup, (dict, list)):
        payload["reply_markup"] = json.dumps(markup, ensure_ascii=False)
    return _request(token, "sendMessage", json=payload)


def inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    """Собрать инлайн-клавиатуру из рядов (текст, callback_data).

    ``inline_keyboard([[("Да", "cb:/send"), ("Нет", "cb:/cancel")]])``
    """
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row]
            for row in rows
        ]
    }


def send_photo(
    token: str,
    chat_id: int | str,
    photo: str,
    caption: str | None = None,
    **kwargs: Any,
) -> dict:
    """Отправить фото. photo — file_id, URL или путь к локальному файлу."""
    return _send_media(token, "sendPhoto", chat_id, photo, caption, **kwargs)


def send_document(
    token: str,
    chat_id: int | str,
    document: str,
    caption: str | None = None,
    **kwargs: Any,
) -> dict:
    """Отправить документ. document — file_id, URL или путь к файлу."""
    return _send_media(token, "sendDocument", chat_id, document, caption, **kwargs)


def send_video(
    token: str,
    chat_id: int | str,
    video: str,
    caption: str | None = None,
    **kwargs: Any,
) -> dict:
    """Отправить видео. video — file_id, URL или путь к файлу."""
    return _send_media(token, "sendVideo", chat_id, video, caption, **kwargs)


def _send_media(
    token: str,
    method: str,
    chat_id: int | str,
    media: str,
    caption: str | None,
    **kwargs: Any,
) -> dict:
    """Общая отправка медиа: локальный файл — multipart, иначе JSON."""
    field = _MEDIA_FIELD[method]
    payload: dict[str, Any] = {"chat_id": chat_id, **kwargs}
    if caption:
        payload["caption"] = caption

    path = Path(media)
    if path.is_file():
        with path.open("rb") as fh:
            return _request(
                token, method,
                data={k: str(v) for k, v in payload.items()},
                files={field: (path.name, fh)},
            )
    payload[field] = media
    return _request(token, method, json=payload)


def get_chat_member(token: str, chat_id: int | str, user_id: int) -> dict:
    """Статус участника чата. Бросает TgError, если бот не в чате."""
    return _request(
        token, "getChatMember",
        json={"chat_id": chat_id, "user_id": user_id},
    )


def get_me_id(token: str) -> int | None:
    """id самого бота (для проверки прав в канале)."""
    try:
        return int(get_me(token).get("id"))
    except (TgError, TypeError, ValueError):
        return None


def delete_message(token: str, chat_id: int | str, message_id: int) -> bool:
    """Удалить сообщение. True — удалено."""
    try:
        _request(token, "deleteMessage",
                 json={"chat_id": chat_id, "message_id": message_id})
        return True
    except TgError:
        return False


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
