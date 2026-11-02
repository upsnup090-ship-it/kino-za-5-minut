"""Ручной постинг — публикация сообщений в каналы клиента."""

from __future__ import annotations

import logging
from typing import Any

from project_ruch.ruch_db import RuchDb

logger = logging.getLogger(__name__)


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


def publish_post(
    db: RuchDb,
    post_id: int,
    token: str,
    message_id: int | None = None,
) -> bool:
    """Отметить пост как отправленный.

    Реальная отправка делается через tg_api из pro_code.
    Здесь — только отметка в БД.
    """
    db.mark_sent(post_id, message_id)
    return True


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
