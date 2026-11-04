"""Мост ручного постинга → rezerv_post.

Ручной модуль пишет отложенные посты в ``rezerv_post`` (своя БД, своя схема).
``kino_auto_db.reserve`` остаётся только для автопостинга — не смешиваем.

Поток: ``add_to_reserve`` (черновик → резерв) → ``publish_next`` (взять
следующий и отправить через ``manual_post.publish_post``).
"""

from __future__ import annotations

import logging
from typing import Any

from pro_code.tg_api import TgError
from project_ruch import manual_post
from project_ruch.ruch_db import RuchDb
from project_ruch.rezerv_post.rezerv_db import ReserveDb

logger = logging.getLogger(__name__)


def add_to_reserve(
    ruch_db: RuchDb,
    user_id: int,
    channel_chat_id: int,
    text: str,
    media_type: str = "text",
    media_path: str | None = None,
    parse_mode: str = "HTML",
    scheduled_at: str | None = None,
) -> int:
    """Положить пост в резерв ручного модуля. Возвращает id записи."""
    reserve = ReserveDb()
    try:
        return reserve.add(
            user_id=user_id,
            text=text,
            source="manual",
            media_type=media_type,
            media_path=media_path,
            parse_mode=parse_mode,
            scheduled_at=scheduled_at,
        )
    finally:
        reserve.close()


def list_pending(user_id: int) -> list[dict[str, Any]]:
    """Отложенные посты клиента, ещё не истёкшие."""
    reserve = ReserveDb()
    try:
        return reserve.list_pending(user_id)
    finally:
        reserve.close()


def count_pending(user_id: int) -> int:
    reserve = ReserveDb()
    try:
        return reserve.count_pending(user_id)
    finally:
        reserve.close()


def publish_next(
    ruch_db: RuchDb,
    user_id: int,
    token: str,
    channel_chat_id: int,
) -> dict[str, Any]:
    """Взять следующий отложенный пост и отправить его.

    Возвращает ``{ok, post_id, message_id, error}``. Если резерв пуст —
    ``{ok: False, error: 'резерв пуст'}``.
    """
    reserve = ReserveDb()
    try:
        item = reserve.take_next(user_id)
    finally:
        reserve.close()

    if not item:
        return {"ok": False, "error": "резерв пуст"}

    return _publish_item(ruch_db, user_id, token, channel_chat_id, item)


def publish_by_id(
    ruch_db: RuchDb,
    user_id: int,
    token: str,
    channel_chat_id: int,
    reserve_id: int,
) -> dict[str, Any]:
    """Опубликовать конкретный отложенный пост (по id) в выбранный канал.

    Возвращает ``{ok, post_id, message_id, error}``. Если записи нет или она
    не принадлежит клиенту — ``{ok: False, error: ...}``.
    """
    reserve = ReserveDb()
    try:
        item = reserve.get(reserve_id)
    finally:
        reserve.close()

    if not item:
        return {"ok": False, "error": f"отложенный пост #{reserve_id} не найден"}
    if int(item.get("user_id")) != user_id:
        return {"ok": False, "error": f"отложенный пост #{reserve_id} не твой"}
    if item.get("status") != "pending":
        return {"ok": False, "error": f"отложенный пост #{reserve_id} уже использован"}

    return _publish_item(ruch_db, user_id, token, channel_chat_id, item)


def _publish_item(
    ruch_db: RuchDb,
    user_id: int,
    token: str,
    channel_chat_id: int,
    item: dict[str, Any],
) -> dict[str, Any]:
    """Общая публикация отложенного поста: черновик → manual_post.publish_post."""
    post_id = ruch_db.add_manual_post(
        user_id=user_id,
        channel_chat_id=channel_chat_id,
        text=item.get("text") or "",
        media_type=item.get("media_type") or "text",
        media_path=item.get("media_path"),
        parse_mode=item.get("parse_mode") or "HTML",
    )
    result = manual_post.publish_post(ruch_db, post_id, token)
    result["post_id"] = post_id
    result["reserve_id"] = item["id"]
    if result.get("ok"):
        # Отметить запись использованной, чтобы не публиковать дважды.
        _mark_used(item["id"])
    return result


def _mark_used(reserve_id: int) -> None:
    """Пометить запись резерва использованной (status='used')."""
    reserve = ReserveDb()
    try:
        reserve.conn.execute(
            "UPDATE reserve SET status = 'used', used_at = ? WHERE id = ?",
            (reserve._now(), reserve_id),
        )
        reserve.conn.commit()
    finally:
        reserve.close()


def purge_expired() -> int:
    """Удалить истёкшие и использованные записи резерва."""
    reserve = ReserveDb()
    try:
        return reserve.purge_expired()
    finally:
        reserve.close()
