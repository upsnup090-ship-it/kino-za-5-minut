"""Повторяющиеся сообщения — управление расписанием.

Время в БД (``time``, ``start_date``, ``end_date``) задано в зоне клиента
(``RuchConfig.timezone``, по умолчанию Europe/Moscow), а не в UTC. Раньше
``get_due_messages`` сравнивал с ``datetime.now(timezone.utc)`` — сообщение
на 09:00 уходило в 12:00 по Москве.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from project_ruch.config import RuchConfig
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


def _parse_date(raw: str | None) -> date | None:
    """Дата из БД: YYYY-MM-DD или ISO-строка. None — если пусто/битая."""
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        logger.warning("не разобрал дату %r, считаю отсутствующей", raw)
        return None


def _parse_time(raw: str | None) -> str | None:
    """Время из БД в HH:MM. Принимает HH:MM и HH:MM:SS."""
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _split_days(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [d.strip() for d in raw.split(",") if d.strip()]


def get_due_messages(
    db: RuchDb,
    user_id: int,
    now: datetime | None = None,
    tz: ZoneInfo | None = None,
) -> list[dict[str, Any]]:
    """Сообщения, которые нужно отправить сейчас (по расписанию).

    ``now`` — момент проверки (тесты); по умолчанию текущее время.
    ``tz`` — зона клиента; по умолчанию из RuchConfig.

    Проверяет время, день недели, день месяца, даты начала/окончания.
    Всё считается в зоне клиента, а не в UTC.
    """
    zone = tz or RuchConfig().timezone
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(zone)

    current_time = local.strftime("%H:%M")
    current_weekday = str(local.weekday())  # 0=пн
    current_day = str(local.day)
    today = local.date()

    due = []
    for msg in db.list_repeating(user_id):
        if not msg["enabled"]:
            continue

        start = _parse_date(msg.get("start_date"))
        if start and today < start:
            continue
        end = _parse_date(msg.get("end_date"))
        if end and today > end:
            continue

        days_week = _split_days(msg.get("days_week"))
        if days_week and current_weekday not in days_week:
            continue

        days_month = _split_days(msg.get("days_month"))
        if days_month and current_day not in days_month:
            continue

        if _parse_time(msg.get("time")) == current_time:
            due.append(msg)

    return due