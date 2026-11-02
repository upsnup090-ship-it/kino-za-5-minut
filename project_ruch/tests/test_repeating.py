"""Тесты расписания повторяющихся сообщений.

Главный регресс: время в БД задано в зоне клиента, а не в UTC. Раньше
``get_due_messages`` сравнивал с ``datetime.now(timezone.utc)`` — пост на
09:00 уходил в 12:00 по Москве.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from project_ruch import repeating as rp

MSK = ZoneInfo("Europe/Moscow")
UTC = timezone.utc


def _at(y, m, d, h, mi, tz=UTC):
    return datetime(y, m, d, h, mi, tzinfo=tz)


def test_due_at_local_time(ruch_db):
    """09:00 MSK = 06:00 UTC — пост должен сработать."""
    rp.add_repeating(ruch_db, 1, "утро", "09:00")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 6, 0), tz=MSK)
    assert len(due) == 1


def test_not_due_at_utc_time(ruch_db):
    """09:00 UTC = 12:00 MSK — пост НЕ должен сработать (старый баг)."""
    rp.add_repeating(ruch_db, 1, "утро", "09:00")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 9, 0), tz=MSK)
    assert due == []


def test_due_in_other_zone(ruch_db):
    """Тот же пост в зоне UTC срабатывает в 09:00 UTC."""
    rp.add_repeating(ruch_db, 1, "утро", "09:00")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 9, 0), tz=UTC)
    assert len(due) == 1


def test_disabled_not_due(ruch_db):
    mid = rp.add_repeating(ruch_db, 1, "утро", "09:00")["id"]
    rp.toggle_repeating(ruch_db, mid)
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 6, 0), tz=MSK)
    assert due == []


def test_wrong_minute(ruch_db):
    rp.add_repeating(ruch_db, 1, "утро", "09:00")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 6, 1), tz=MSK)
    assert due == []


def test_time_with_seconds_accepted(ruch_db):
    """В БД может лежать 09:00:00 — сравниваем по HH:MM."""
    rp.add_repeating(ruch_db, 1, "утро", "09:00:00")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 6, 0), tz=MSK)
    assert len(due) == 1


def test_weekday_filter(ruch_db):
    """2026-11-02 — понедельник (0). Пост только на вторник не срабатывает."""
    rp.add_repeating(ruch_db, 1, "вторник", "09:00", days_week="1")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 6, 0), tz=MSK)
    assert due == []


def test_weekday_match(ruch_db):
    rp.add_repeating(ruch_db, 1, "понедельник", "09:00", days_week="0")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 6, 0), tz=MSK)
    assert len(due) == 1


def test_weekday_uses_local_date(ruch_db):
    """23:30 UTC воскресенья = 02:30 понедельника MSK → день недели 0."""
    rp.add_repeating(ruch_db, 1, "ночь", "02:30", days_week="0")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 1, 23, 30), tz=MSK)
    assert len(due) == 1


def test_day_of_month(ruch_db):
    rp.add_repeating(ruch_db, 1, "первое", "09:00", days_month="1")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 6, 0), tz=MSK)
    assert due == []


def test_day_of_month_match(ruch_db):
    rp.add_repeating(ruch_db, 1, "второе", "09:00", days_month="2")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 6, 0), tz=MSK)
    assert len(due) == 1


def test_start_date_future(ruch_db):
    rp.add_repeating(ruch_db, 1, "позже", "09:00", start_date="2026-12-01")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 6, 0), tz=MSK)
    assert due == []


def test_start_date_passed(ruch_db):
    rp.add_repeating(ruch_db, 1, "уже", "09:00", start_date="2026-11-01")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 6, 0), tz=MSK)
    assert len(due) == 1


def test_end_date_passed(ruch_db):
    rp.add_repeating(ruch_db, 1, "кончилось", "09:00", end_date="2026-11-01")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 6, 0), tz=MSK)
    assert due == []


def test_end_date_today_inclusive(ruch_db):
    """Последний день диапазона включён."""
    rp.add_repeating(ruch_db, 1, "последний", "09:00", end_date="2026-11-02")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 6, 0), tz=MSK)
    assert len(due) == 1


def test_date_compared_as_date_not_string(ruch_db):
    """Регресс: строковое сравнение ISO ломалось на '2026-11-2' vs '2026-11-02'."""
    rp.add_repeating(ruch_db, 1, "x", "09:00", start_date="2026-11-2")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 6, 0), tz=MSK)
    assert len(due) == 1


def test_broken_date_ignored(ruch_db):
    """Битая дата не роняет проверку — считается отсутствующей."""
    rp.add_repeating(ruch_db, 1, "x", "09:00", start_date="не дата")
    due = rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 6, 0), tz=MSK)
    assert len(due) == 1


def test_naive_now_treated_as_utc(ruch_db):
    rp.add_repeating(ruch_db, 1, "утро", "09:00")
    due = rp.get_due_messages(ruch_db, 1, now=datetime(2026, 11, 2, 6, 0), tz=MSK)
    assert len(due) == 1


def test_isolated_per_user(ruch_db):
    rp.add_repeating(ruch_db, 1, "user1", "09:00")
    rp.add_repeating(ruch_db, 2, "user2", "09:00")
    assert len(rp.get_due_messages(ruch_db, 1, now=_at(2026, 11, 2, 6, 0), tz=MSK)) == 1
    assert len(rp.get_due_messages(ruch_db, 2, now=_at(2026, 11, 2, 6, 0), tz=MSK)) == 1


def test_default_timezone_is_moscow():
    from project_ruch.config import RuchConfig

    assert str(RuchConfig().timezone) == "Europe/Moscow"


def test_timezone_from_env(monkeypatch):
    from project_ruch.config import _resolve_timezone

    monkeypatch.setenv("RUCH_TIMEZONE", "Asia/Novosibirsk")
    assert str(_resolve_timezone()) == "Asia/Novosibirsk"


def test_bad_timezone_falls_back(monkeypatch):
    from project_ruch.config import _resolve_timezone

    monkeypatch.setenv("RUCH_TIMEZONE", "Mars/Olympus")
    assert str(_resolve_timezone()) == "Europe/Moscow"
