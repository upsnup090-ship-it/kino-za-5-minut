"""Тесты админки: новая иерархия и старые слоты/постинг/команды."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from kino.admin import (
    Reply,
    check_add,
    day_count,
    handle_callback,
    handle_command,
    is_admin,
    screen_autoposting,
    screen_backup,
    screen_home,
    screen_preview,
    screen_runs,
    screen_schedule,
    screen_slot,
    screen_today,
    validate_time,
    validate_weekday,
    warn_slots_per_day,
)
from kino.config import Config, Settings
from kino.schedule_db import ScheduleDb


def _cfg(tmp_path: Path, admin_chat_id: str = "-100500") -> Config:
    return Config(
        sources=[],
        weights={"news": 1},
        bad=[],
        sad=[],
        settings=Settings(
            schedule_catchup_minutes=30,
            schedule_late_grace_minutes=20,
            timezone="Europe/Moscow",
        ),
        admin_chat_id=admin_chat_id,
        state_path=tmp_path / "state.json",
        db_path=tmp_path / "kino.db",
    )


def _now() -> datetime:
    return datetime(2026, 10, 31, 18, 0, tzinfo=ZoneInfo("Europe/Moscow"))


def _data(reply) -> list[str]:
    return [btn["callback_data"] for row in reply.keyboard for btn in row]


# --- доступ ------------------------------------------------------------------


def test_admin_allowed(tmp_path):
    cfg = _cfg(tmp_path)
    assert is_admin(cfg, "-100500") is True
    assert is_admin(cfg, -100500) is True


def test_foreign_chat_denied(tmp_path):
    cfg = _cfg(tmp_path)
    assert is_admin(cfg, "123") is False
    assert is_admin(cfg, None) is False


def test_empty_admin_id_denies_everyone(tmp_path):
    cfg = _cfg(tmp_path, admin_chat_id="")
    assert is_admin(cfg, "-100500") is False
    assert is_admin(cfg, "") is False


# --- валидация ---------------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [("21:20", "21:20"), ("9:05", "09:05"), (" 23:40 ", "23:40")])
def test_validate_time_ok(raw, expected):
    result = validate_time(raw)
    assert result.ok and result.value == expected


@pytest.mark.parametrize("bad", ["", "25:00", "11:60", "abc", "1120"])
def test_validate_time_rejects(bad):
    result = validate_time(bad)
    assert not result.ok
    assert result.error


@pytest.mark.parametrize(("raw", "expected"), [("пт", "4"), ("пн", "0"), ("вс", "6"), ("3", "3")])
def test_validate_weekday_ok(raw, expected):
    result = validate_weekday(raw)
    assert result.ok and result.value == expected


@pytest.mark.parametrize("bad", ["понедельник", "8", "", "mon"])
def test_validate_weekday_rejects(bad):
    assert not validate_weekday(bad).ok


def test_check_add_rejects_duplicate(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).add_slot(4, "23:40")
    result = check_add(cfg, 4, "23:40")
    assert not result.ok
    assert "уже есть" in result.error


def test_check_add_allows_new(tmp_path):
    cfg = _cfg(tmp_path)
    assert check_add(cfg, 4, "23:40").ok


def test_warn_slots_per_day():
    assert warn_slots_per_day(10) == ""
    assert "больше" in warn_slots_per_day(11)


# --- экраны ------------------------------------------------------------------


def test_home_is_greeting(tmp_path):
    reply = screen_home(_cfg(tmp_path))
    assert "👋 Приветствие" in reply.text
    assert "Выбери раздел" in reply.text
    assert "Время бота:" in reply.text
    assert _data(reply) == ["nav:autoposting", "nav:about", "nav:backup"]


def test_autoposting_screen_shows_state(tmp_path):
    cfg = _cfg(tmp_path)
    reply = screen_autoposting(cfg)
    assert "🤖 Автопостинг ТГ" in reply.text
    assert "цикл:" in reply.text
    assert "nav:state" in _data(reply)
    assert "nav:state:errors" in _data(reply)
    assert "nav:runnow" in _data(reply)
    assert "nav:sched" in _data(reply)
    assert "nav:runs:0" in _data(reply)
    assert "nav:home" in _data(reply)


def test_about_screen(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "nav:about", _now())
    assert "ℹ️ О боте" in reply.text
    assert "Кино за 5 минут" in reply.text
    assert _data(reply) == ["nav:home"]


def test_backup_screen(tmp_path):
    reply = screen_backup(_cfg(tmp_path))
    assert "🗄 Бэкап+логи" in reply.text
    assert _data(reply) == ["nav:backup:logs", "nav:backup:make", "nav:home"]


def test_nav_to_autoposting(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "nav:autoposting", _now())
    assert "🤖 Автопостинг ТГ" in reply.text


def test_unknown_nav_returns_home(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "nav:nonsense", _now())
    assert "👋 Приветствие" in reply.text
    assert "Выбери раздел" in reply.text
    assert "Время бота:" in reply.text


# --- команды -----------------------------------------------------------------


def test_command_start_matches_home(tmp_path):
    cfg = _cfg(tmp_path)
    assert handle_command(cfg, "/start", _now()).text == screen_home(cfg).text


def test_command_admin_matches_home(tmp_path):
    cfg = _cfg(tmp_path)
    assert handle_command(cfg, "/admin", _now()).text == screen_home(cfg).text


def test_command_help(tmp_path):
    reply = handle_command(_cfg(tmp_path), "/help", _now())
    assert "Всё на кнопках" in reply.text


def test_command_schedule_matches_button_screen(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    from_command = handle_command(cfg, "/schedule", _now())
    from_button = handle_callback(cfg, "nav:sched", _now())
    assert from_command.text == from_button.text
    assert from_command.keyboard == from_button.keyboard


def test_command_schedule_with_bot_suffix(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    reply = handle_command(cfg, "/schedule@kino_bot", _now())
    assert "Расписание публикаций" in reply.text


def test_command_slot_add(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    reply = handle_command(cfg, "/slot add сб 09:15", _now())
    assert "Добавлен слот сб 09:15" in reply.text
    assert ScheduleDb(cfg.db_path).get_slot(5, "09:15") is not None


def test_command_slot_add_duplicate(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    reply = handle_command(cfg, "/slot add пт 23:40", _now())
    assert "уже есть" in reply.text


def test_command_slot_add_bad_time(tmp_path):
    cfg = _cfg(tmp_path)
    reply = handle_command(cfg, "/slot add пт 25:00", _now())
    assert "не время" in reply.text


def test_command_slot_add_bad_day(tmp_path):
    cfg = _cfg(tmp_path)
    reply = handle_command(cfg, "/slot add понедельник 10:00", _now())
    assert "не день недели" in reply.text


def test_command_slot_del(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    reply = handle_command(cfg, "/slot del пт 23:40", _now())
    assert "удалён" in reply.text


def test_command_slot_off_on(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.seed_defaults()
    reply = handle_command(cfg, "/slot off вт 21:20", _now())
    assert "выключен" in reply.text
    slot = db.get_slot(1, "21:20")
    assert slot is not None and slot.enabled is False
    reply = handle_command(cfg, "/slot on вт 21:20", _now())
    assert "включён" in reply.text


def test_command_slot_clear(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    reply = handle_command(cfg, "/slot clear пт", _now())
    assert "Удалено слотов: 3" in reply.text
    assert day_count(cfg, 4) == 0


def test_command_slot_without_args(tmp_path):
    reply = handle_command(_cfg(tmp_path), "/slot", _now())
    assert "Нужно действие" in reply.text


def test_command_unknown_shows_help(tmp_path):
    reply = handle_command(_cfg(tmp_path), "/nonsense", _now())
    assert "/schedule" in reply.text


def test_command_empty_text_shows_help(tmp_path):
    assert "/slot" in handle_command(_cfg(tmp_path), "", _now()).text


def test_command_next(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    reply = handle_command(cfg, "/next", _now())
    assert "Ближайшие 7 дней" in reply.text


# --- кнопки ------------------------------------------------------------------


def test_callback_seed(tmp_path):
    cfg = _cfg(tmp_path)
    reply = handle_callback(cfg, "seed", _now())
    assert "Сетка заполнена" in reply.text
    assert ScheduleDb(cfg.db_path).count_slots() == 13


def test_callback_slot_opens_card(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    slot = db.add_slot(4, "23:40")
    reply = handle_callback(cfg, f"slot:{slot.id}", _now())
    assert "пт 23:40" in reply.text


def test_callback_off_toggles(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    slot = db.add_slot(4, "23:40")
    reply = handle_callback(cfg, f"off:{slot.id}", _now())
    assert "выключен" in reply.text
    assert db.get_slot(4, "23:40").enabled is False  # type: ignore[union-attr]


def test_callback_del_removes(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    slot = db.add_slot(4, "23:40")
    reply = handle_callback(cfg, f"del:{slot.id}", _now())
    assert "удалён" in reply.text
    assert db.get_slot(4, "23:40") is None


def test_callback_edit_prompts_for_time(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    slot = db.add_slot(4, "23:40")
    reply = handle_callback(cfg, f"edit:{slot.id}", _now())
    assert "Новое время" in reply.text


def test_callback_add_prompts(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "add", _now())
    assert "выбери день недели" in reply.text
    data = [b["callback_data"] for row in reply.keyboard for b in row]
    assert "add:day:4" in data


def test_callback_add_day_prompts_with_command(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "add:day:4", _now())
    assert "/slot add пт HH:MM" in reply.text


def test_callback_unknown_returns_home(tmp_path):
    cfg = _cfg(tmp_path)
    reply = handle_callback(cfg, "garbage", _now())
    assert "📅 Расписание пусто" in reply.text or reply.text == "👋 Приветствие\n\nВыбери раздел:"


def test_callback_add_day_bad_falls_back(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    reply = handle_callback(cfg, "add:day:9", _now())
    assert "Расписание публикаций" in reply.text


def test_callback_broken_id_falls_back(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    reply = handle_callback(cfg, "slot:notanumber", _now())
    assert "Расписание публикаций" in reply.text


def test_reply_markup_none_without_buttons():
    assert Reply(text="x").markup() is None
    assert Reply(text="x", keyboard=[[{"text": "a", "callback_data": "b"}]]).markup() is not None
