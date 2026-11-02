"""Ручной постинг — публикация сообщений в каналы клиента.

Поток: ``create_post`` (черновик в БД) → ``publish_post`` (реальная отправка
через Bot API клиента) → статус ``sent`` + ``message_id`` в ``post_stats``.

Медиа: ``media_path`` хранит либо локальный путь (до первой отправки), либо
``file_id`` (после). ``publish_post`` после успешной отправки перезаписывает
``media_path`` на ``file_id`` — повторная публикация не грузит байты заново.
"""

from __future__ import annotations

import logging
from typing import Any

from pro_code.tg_api import TgError, send_message
from project_ruch.media import MediaError, resolve_media, send_media, validate
from project_ruch.ruch_db import RuchDb

logger = logging.getLogger(__name__)

#: Лимит подписи Telegram (символы).
CAPTION_LIMIT = 1024

#: Лимит текста сообщения Telegram (символы).
TEXT_LIMIT = 4096


class PostError(Exception):
    """Ошибка публикации ручного поста."""


def create_post(
    db: RuchDb,
    user_id: int,
    channel_chat_id: int,
    text: str,
    media_type: str = "text",
    media_path: str | None = None,
    parse_mode: str = "HTML",
    reply_to_msg_id: int | None = None,
) -> dict[str, Any]:
    """Создать черновик ручного поста."""
    post_id = db.add_manual_post(
        user_id=user_id,
        channel_chat_id=channel_chat_id,
        text=text,
        media_type=media_type,
        media_path=media_path,
        parse_mode=parse_mode,
        reply_to_msg_id=reply_to_msg_id,
    )
    return {"id": post_id, "status": "draft"}


def _check_limits(text: str, media_type: str) -> None:
    """Проверить лимиты Telegram до отправки."""
    limit = CAPTION_LIMIT if media_type != "text" else TEXT_LIMIT
    if len(text) > limit:
        raise PostError(
            f"текст длиннее лимита Telegram ({len(text)} > {limit})"
        )


def publish_post(
    db: RuchDb,
    post_id: int,
    token: str,
    message_id: int | None = None,
) -> dict[str, Any]:
    """Отправить пост в канал клиента его ботом.

    Возвращает ``{ok, message_id, file_id, error}``. Статус в БД:
    ``sent`` при успехе, ``failed`` при ошибке.
    """
    post = db.get_post(post_id)
    if not post:
        return {"ok": False, "error": f"пост {post_id} не найден"}

    if post["status"] == "sent":
        return {
            "ok": True,
            "message_id": db.get_message_id(post_id),
            "already_sent": True,
        }

    text = post.get("text") or ""
    media_type = post.get("media_type") or "text"
    media_path = post.get("media_path")
    parse_mode = post.get("parse_mode") or "HTML"
    reply_to = post.get("reply_to_msg_id")
    chat_id = post["channel_chat_id"]

    try:
        _check_limits(text, media_type)
        if media_type != "text":
            validate(media_path or "", media_type)
    except (PostError, MediaError) as exc:
        logger.warning("пост %d не прошёл проверку: %s", post_id, exc)
        db.mark_failed(post_id)
        return {"ok": False, "error": str(exc)}

    kwargs: dict[str, Any] = {}
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    if reply_to:
        kwargs["reply_to_message_id"] = reply_to

    try:
        if media_type == "text":
            result = send_message(token, chat_id, text, **kwargs)
            sent_id = result.get("message_id")
            file_id = None
        else:
            media = resolve_media(media_path, media_type)
            if not media:
                raise PostError(f"медиа {media_type} без пути")
            res = send_media(
                token, chat_id, media_type, media, text or None,
                parse_mode=parse_mode, reply_to_msg_id=reply_to,
            )
            if not res.get("ok"):
                raise PostError(res.get("error") or "медиа не отправлено")
            sent_id = res.get("message_id")
            file_id = res.get("file_id")
    except (TgError, PostError) as exc:
        logger.warning("пост %d не отправлен: %s", post_id, exc)
        db.mark_failed(post_id)
        return {"ok": False, "error": str(exc)}

    if not sent_id:
        db.mark_failed(post_id)
        return {"ok": False, "error": "Telegram не вернул message_id"}

    db.mark_sent(post_id, sent_id)
    if file_id:
        _cache_file_id(db, post_id, file_id)

    logger.info("пост %d отправлен в %s (message_id=%s)", post_id, chat_id, sent_id)
    return {"ok": True, "message_id": sent_id, "file_id": file_id}


def _cache_file_id(db: RuchDb, post_id: int, file_id: str) -> None:
    """Заменить локальный путь на file_id — повторная отправка без загрузки."""
    db.conn.execute(
        "UPDATE manual_posts SET media_path = ? WHERE id = ?", (file_id, post_id)
    )
    db.conn.commit()


def delete_last_post(db: RuchDb, user_id: int, token: str) -> dict[str, Any]:
    """Удалить последний отправленный пост из канала и из БД."""
    from pro_code.tg_api import delete_message

    posts = db.get_last_posts(user_id, n=1)
    if not posts:
        return {"ok": False, "error": "нет отправленных постов"}
    post = posts[0]
    msg_id = db.get_message_id(post["id"])
    if not msg_id:
        return {"ok": False, "error": "message_id не найден"}
    deleted = delete_message(token, post["channel_chat_id"], msg_id)
    if deleted:
        db.delete_post(post["id"])
    return {"ok": deleted, "post_id": post["id"], "message_id": msg_id}


def list_posts(
    db: RuchDb,
    user_id: int,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Список ручных постов пользователя."""
    return db.list_manual_posts(user_id, limit=limit, offset=offset)


def get_last_posts(db: RuchDb, user_id: int, n: int = 5) -> list[dict[str, Any]]:
    """Последние N отправленных постов (для бэкапа)."""
    return db.get_last_posts(user_id, n=n)
