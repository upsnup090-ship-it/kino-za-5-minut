"""Статистика клиента (модуль project_ruch/stats).

Команда `/stats` в боте клиента — показатели его бота:
посты, просмотры, пересылки, повторяющиеся, каналы, резерв.
"""

from __future__ import annotations

import logging
from typing import Any

from pro_code.clients_db import ClientsDb
from pro_code.tg_api import TgError, send_message
from project_ruch.ruch_db import RuchDb

logger = logging.getLogger(__name__)


def _fmt(n: Any) -> str:
    try:
        return f"{int(n):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"


def user_stats_text(clients_db: ClientsDb, ruch_db: RuchDb, user_id: int) -> str:
    """Экран статистики для клиента."""
    s = ruch_db.get_stats(user_id)
    n_repeating = len(ruch_db.list_repeating(user_id))
    n_enabled = sum(1 for m in ruch_db.list_repeating(user_id) if m.get("enabled"))
    n_channels = len(clients_db.list_channels(user_id))
    n_backups = len(ruch_db.list_backups(user_id))
    return (
        "📊 **Твоя статистика**\n\n"
        f"✍️ Опубликовано постов: **{_fmt(s.get('total_posts'))}**\n"
        f"👁 Просмотры: **{_fmt(s.get('total_views'))}**\n"
        f"↗️ Пересылки: **{_fmt(s.get('total_forwards'))}**\n"
        f"🔁 Повторяющихся: **{n_repeating}** (активных {n_enabled})\n"
        f"📢 Каналов подключено: **{n_channels}**\n"
        f"💾 Бэкапов сохранено: **{n_backups}** (2 последних хранятся)"
    )


def send_stats(token: str, chat_id: int, user_id: int, clients_db: ClientsDb) -> None:
    ruch_db = RuchDb()
    try:
        text = user_stats_text(clients_db, ruch_db, user_id)
    finally:
        ruch_db.close()
    try:
        send_message(token, chat_id, text)
    except TgError as exc:
        logger.warning("не удалось отправить статистику: %s", exc)
