"""Медиа ручного постинга: загрузка в Telegram и кэш file_id.

Правило: файл грузится в Telegram один раз. После первой отправки Telegram
возвращает ``file_id`` — он и хранится в ``manual_posts.media_path``. Повторная
публикация того же поста идёт по ``file_id``, без повторной загрузки байтов.

Типы: ``text`` (без медиа), ``photo``, ``document``, ``video``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pro_code.tg_api import (
    TgError,
    send_document,
    send_photo,
    send_video,
)

logger = logging.getLogger(__name__)

#: Допустимые типы медиа ручного поста.
MEDIA_TYPES = ("text", "photo", "document", "video")

#: Лимиты Telegram (байты). Больше — Telegram отклонит загрузку.
MAX_SIZE = {
    "photo": 10 * 1024 * 1024,
    "video": 50 * 1024 * 1024,
    "document": 50 * 1024 * 1024,
}

#: Расширения, которые Telegram примет как фото.
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}

#: Расширения, которые Telegram примет как видео.
VIDEO_EXT = {".mp4", ".mov", ".m4v"}


class MediaError(Exception):
    """Ошибка медиа: тип, размер, отсутствующий файл."""


def detect_media_type(path: str | Path) -> str:
    """Определить тип медиа по расширению. Неизвестное — document."""
    ext = Path(path).suffix.lower()
    if ext in PHOTO_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    return "document"


def validate(path: str | Path, media_type: str) -> None:
    """Проверить файл до отправки. Бросает MediaError.

    URL и file_id — не файлы: их проверять нечем, Telegram сам разберётся.
    """
    if media_type not in MEDIA_TYPES:
        raise MediaError(f"неизвестный тип медиа: {media_type}")
    if media_type == "text":
        return
    raw = str(path)
    if raw.startswith(("http://", "https://")):
        return
    if _looks_like_file_id(raw):
        return
    p = Path(raw)
    if not p.is_file():
        raise MediaError(f"файл не найден: {p}")
    limit = MAX_SIZE.get(media_type)
    if limit and p.stat().st_size > limit:
        raise MediaError(
            f"файл больше лимита Telegram для {media_type}: "
            f"{p.stat().st_size} > {limit}"
        )


def _looks_like_file_id(value: str) -> bool:
    """file_id Telegram: длинная строка без слэшей и пробелов."""
    if not value or "/" in value or " " in value:
        return False
    return len(value) >= 20


def _extract_file_id(result: dict, media_type: str) -> str | None:
    """Достать file_id из ответа Bot API."""
    if media_type == "photo":
        sizes = result.get("photo") or []
        if sizes:
            # Последний элемент — самый большой размер.
            return sizes[-1].get("file_id")
        return None
    if media_type == "document":
        return (result.get("document") or {}).get("file_id")
    if media_type == "video":
        return (result.get("video") or {}).get("file_id")
    return None


def send_media(
    token: str,
    chat_id: int | str,
    media_type: str,
    media: str,
    caption: str | None = None,
    parse_mode: str | None = None,
    reply_to_msg_id: int | None = None,
) -> dict[str, Any]:
    """Отправить медиа в чат. Возвращает {ok, message_id, file_id, error}.

    ``media`` — file_id, URL или локальный путь. Локальный путь грузится
    multipart-ом, file_id уходит как есть.
    """
    if media_type not in MEDIA_TYPES:
        return {"ok": False, "error": f"неизвестный тип медиа: {media_type}"}
    if media_type == "text":
        return {"ok": False, "error": "text — не медиа, используй send_message"}

    kwargs: dict[str, Any] = {}
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    if reply_to_msg_id:
        kwargs["reply_to_message_id"] = reply_to_msg_id

    try:
        if media_type == "photo":
            result = send_photo(token, chat_id, media, caption, **kwargs)
        elif media_type == "video":
            result = send_video(token, chat_id, media, caption, **kwargs)
        else:
            result = send_document(token, chat_id, media, caption, **kwargs)
    except TgError as exc:
        logger.warning("медиа не отправлено (%s): %s", media_type, exc)
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "message_id": result.get("message_id"),
        "file_id": _extract_file_id(result, media_type),
    }


def resolve_media(media_path: str | None, media_type: str) -> str | None:
    """Что отправить: file_id из кэша или локальный путь.

    ``media_path`` хранит либо file_id (после первой отправки), либо путь.
    """
    if not media_path:
        return None
    if media_type == "text":
        return None
    return media_path
