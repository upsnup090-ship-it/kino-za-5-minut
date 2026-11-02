"""Повторяющиеся сообщения — управление расписанием."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from project_ruch.ruch_db import RuchDb

logger = logging.getLogger(__name__)


def add_repeating(
    db: RuchDb,
    user_id: int,
    text: str,
    time: str,
    interval_hours: int = 24,
    interval_minutes: int = 0,
    days_week: str = "0,1,2,3,4,5,6",
    days_month: str = "",
    start_date: str | None = None,
    end_date: str | None = None,
    pin: bool = False,
    delete_last: bool = False,
) -> dict[str, Any]:
    """Добавить повторяющееся сообщение."""
    msg_id = db.add_repeating(
        user_id=user_id,
        text=text,
        time=time,
        interval_hours=interval_hours,
        interval_minutes=interval_minutes,
        days_week=days_week,
        days_month=days_month,
        start_date=start_date,
        end_date=end_date,
        pin=pin,
        delete_last=delete_last,
    )
    return {"id": msg_id, "status": "created"}


def list_repeating(db: RuchDb, user_id: int) -> list[dict[str, Any]]:
    """Список повторяющихся сообщений пользователя."""
    return db.list_repeating(user_id)


def toggle_repeating(db: RuchDb, msg_id: int) -> bool:
    """Вкл/выкл сообщение."""
    return db.toggle_repeating(msg_id)


def delete_repeating(db: RuchDb, msg_id: int) -> bool:
    """Удалить сообщение."""
    return db.delete_repeating(msg_id)


def get_due_messages(db: RuchDb, user_id: int) -> list[dict[str, Any]]:
    """Сообщения, которые нужно отправить сейчас (по расписанию).

    Проверяет время, день недели, день месяца, даты начала/окончания.
    """
    now = datetime.now(timezone.utc)
    current_time = now.strftime("%H:%M")
    current_weekday = str(now.weekday())  # 0=пн
    current_day = str(now.day)

    messages = db.list_repeating(user_id)
    due = []
    for msg in messages:
        if not msg["enabled"]:
            continue
        if msg["start_date"] and now.isoformat() < msg["start_date"]:
            continue
        if msg["end_date"] and now.isoformat() > msg["end_date"]:
            continue

        # Проверка дня недели
        if msg["days_week"]:
            days = [d.strip() for d in msg["days_week"].split(",")]
            if current_weekday not in days:
                continue

        # Проверка дня месяца
        if msg["days_month"]:
            days = [d.strip() for d in msg["days_month"].split(",")]
            if current_day not in days:
                continue

        # Проверка времени (HH:MM)
        if msg["time"] == current_time:
            due.append(msg)

    return due
