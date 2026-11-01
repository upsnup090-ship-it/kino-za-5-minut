"""Тесты админки (шаг 10): доступ, экраны, валидация, команды и кнопки.

Логика чистая — сеть не нужна. Транспорт проверяется отдельно, с моком
`kino.tg_api`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from kino.admin import (
    SLOTS_PER_DAY_WARN,
    Reply,
    check_add,
    day_count,
    handle_callback,
    handle_command,
    is_admin,
    screen_preview,
    screen_runs,
    screen_schedule,
    screen_slot,
    validate_time,
    validate_weekday,
    warn_slots_per_day,
)
from kino.config import Config, Settings
from kino.schedule_db import ScheduleDb


def _cfg(tmp_path: Path, admin_chat_id: str = "-100500") -> Config:
    return Config(
        sources=[], weights={"news": 1}, bad=[], sad=[],
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
    """Не задан ADMIN_CHAT_ID — админки нет ни для кого, а не «для всех»."""
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
    assert warn_slots_per_day(SLOTS_PER_DAY_WARN) == ""
    assert "больше" in warn_slots_per_day(SLOTS_PER_DAY_WARN + 1)


# --- экраны ------------------------------------------------------------------


def test_schedule_screen_empty_offers_seed(tmp_path):
    reply = screen_schedule(_cfg(tmp_path))
    assert "пусто" in reply.text
    assert reply.keyboard[0][0]["callback_data"] == "seed"


def test_schedule_screen_lists_grid(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    reply = screen_schedule(cfg)
    assert "пт: 11:05, 20:50, 23:40" in reply.text
    assert "слотов: 13" in reply.text
    # Каждый слот — кнопка.
    buttons = [b for row in reply.keyboard for b in row]
    slot_buttons = [b for b in buttons if b["callback_data"].startswith("slot:")]
    assert len(slot_buttons) == 13


def test_schedule_screen_marks_disabled(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.seed_defaults()
    db.set_enabled(4, "23:40", False)
    reply = screen_schedule(cfg)
    assert "23:40 ⏸" in reply.text


def test_slot_screen_shows_state_and_actions(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    slot = db.add_slot(4, "23:40")
    reply = screen_slot(cfg, slot.id)
    assert "пт 23:40" in reply.text
    assert "включён" in reply.text
    data = [b["callback_data"] for row in reply.keyboard for b in row]
    assert f"off:{slot.id}" in data
    assert f"del:{slot.id}" in data
    assert f"edit:{slot.id}" in data


def test_slot_screen_disabled_offers_enable(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    slot = db.add_slot(4, "23:40", enabled=False)
    reply = screen_slot(cfg, slot.id)
    data = [b["callback_data"] for row in reply.keyboard for b in row]
    assert f"on:{slot.id}" in data


def test_slot_screen_shows_last_run(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    slot = db.add_slot(4, "23:40")
    run_id = db.start_run(slot_id=slot.id, slot_at="2026-10-30T23:40")
    db.finish_run(run_id, outcome="published", reason="опоздание 3 мин")
    reply = screen_slot(cfg, slot.id)
    assert "опоздание 3 мин" in reply.text
    assert "published" in reply.text


def test_slot_screen_missing_slot_falls_back(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    reply = screen_slot(cfg, 9999)
    assert "Расписание публикаций" in reply.text


def test_preview_counts_seven_days(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    reply = screen_preview(cfg, _now())
    assert "итого: 13" in reply.text
    assert "Пн" not in reply.text  # дни по-русски, не %a
    assert "пн" in reply.text


def test_preview_uses_same_function_as_scheduler(tmp_path):
    """Превью и планировщик считают одинаково — это требование шага 10."""
    from kino.schedule import SlotConfig, next_occurrence

    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.seed_defaults()
    reply = screen_preview(cfg, _now())

    slots = [(s.id, s.weekday, s.time) for s in db.list_slots(enabled_only=True)]
    slot_cfg = SlotConfig(catchup_minutes=30, late_grace_minutes=20, tz="Europe/Moscow")
    first = next_occurrence(_now(), slots, slot_cfg)
    assert first is not None
    assert f"{first.when.strftime('%d.%m')}" in reply.text


def test_preview_without_slots(tmp_path):
    reply = screen_preview(_cfg(tmp_path), _now())
    assert "Слотов нет" in reply.text


def test_runs_screen_empty(tmp_path):
    reply = screen_runs(_cfg(tmp_path))
    assert "пуст" in reply.text


def test_runs_screen_shows_records_and_reserve(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    run_id = db.start_run(slot_id=None, slot_at="2026-10-31T21:20")
    db.finish_run(run_id, outcome="published")
    db.add_reserve(link="https://a/1", caption="п")
    reply = screen_runs(cfg)
    assert "published" in reply.text
    assert "Резерв: доступно 1 из 1" in reply.text


# --- команды -----------------------------------------------------------------


def test_command_schedule_matches_button_screen(tmp_path):
    """Команда и кнопка дают одно состояние."""
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    from_command = handle_command(cfg, "/schedule", _now())
    from_button = handle_callback(cfg, "schedule", _now())
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
    assert ScheduleDb(cfg.db_path).get_slot(4, "23:40") is None


def test_command_slot_del_missing(tmp_path):
    cfg = _cfg(tmp_path)
    reply = handle_command(cfg, "/slot del пт 23:40", _now())
    assert "нет" in reply.text


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


def test_command_slot_clear_empty_day_allowed(tmp_path):
    """Пустой день — допустимое состояние, не ошибка."""
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    reply = handle_command(cfg, "/slot clear вс", _now())
    assert "Удалено слотов: 2" in reply.text


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
    assert "23:40" in reply.text


def test_callback_add_prompts(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "add", _now())
    assert "выбери день недели" in reply.text
    data = [b["callback_data"] for row in reply.keyboard for b in row]
    assert "add:day:4" in data


def test_callback_add_day_prompts_with_command(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "add:day:4", _now())
    assert "/slot add пт HH:MM" in reply.text


def test_callback_unknown_returns_schedule(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    reply = handle_callback(cfg, "garbage", _now())
    assert "Расписание публикаций" in reply.text


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


# --- транспорт ---------------------------------------------------------------


def test_poll_once_denies_foreign_chat(tmp_path):
    from kino.admin import poll_once

    cfg = _cfg(tmp_path)
    update = {
        "update_id": 10,
        "message": {"chat": {"id": 777}, "text": "/schedule"},
    }
    with patch("kino.tg_api.get_updates", return_value=[update]), \
         patch("kino.tg_api.send_message") as mock_send:
        offset, handled = poll_once(cfg, None, _now())

    assert offset == 11
    assert handled == 1
    mock_send.assert_called_once()
    assert "Нет доступа" in mock_send.call_args[0][2]


def test_poll_once_serves_admin(tmp_path):
    from kino.admin import poll_once

    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    update = {
        "update_id": 11,
        "message": {"chat": {"id": -100500}, "text": "/schedule"},
    }
    with patch("kino.tg_api.get_updates", return_value=[update]), \
         patch("kino.tg_api.send_message") as mock_send:
        offset, handled = poll_once(cfg, None, _now())

    assert offset == 12
    assert handled == 1
    assert "Расписание публикаций" in mock_send.call_args[0][2]


def test_poll_once_denies_foreign_callback(tmp_path):
    from kino.admin import poll_once

    cfg = _cfg(tmp_path)
    update = {
        "update_id": 12,
        "callback_query": {
            "id": "cb1",
            "from": {"id": 777},
            "message": {"chat": {"id": 777}, "message_id": 5},
            "data": "seed",
        },
    }
    with patch("kino.tg_api.get_updates", return_value=[update]), \
         patch("kino.tg_api.answer_callback") as mock_answer, \
         patch("kino.tg_api.edit_message") as mock_edit:
        poll_once(cfg, None, _now())

    mock_answer.assert_called_once()
    assert "Нет доступа" in mock_answer.call_args[0][2]
    mock_edit.assert_not_called()


def test_poll_once_edits_message_for_callback(tmp_path):
    from kino.admin import poll_once

    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()
    update = {
        "update_id": 13,
        "callback_query": {
            "id": "cb2",
            "from": {"id": -100500},
            "message": {"chat": {"id": -100500}, "message_id": 42},
            "data": "preview",
        },
    }
    with patch("kino.tg_api.get_updates", return_value=[update]), \
         patch("kino.tg_api.answer_callback"), \
         patch("kino.tg_api.edit_message") as mock_edit:
        poll_once(cfg, None, _now())

    mock_edit.assert_called_once()
    assert "Ближайшие 7 дней" in mock_edit.call_args[0][3]


def test_poll_once_survives_broken_update(tmp_path):
    from kino.admin import poll_once

    cfg = _cfg(tmp_path)
    updates = [
        {"update_id": 20, "message": None, "callback_query": None},
        {"update_id": 21, "message": {"chat": {"id": -100500}, "text": "/schedule"}},
    ]
    with patch("kino.tg_api.get_updates", return_value=updates), \
         patch("kino.tg_api.send_message"):
        offset, handled = poll_once(cfg, None, _now())

    assert offset == 22
    assert handled == 1


def test_run_without_admin_id_fails(tmp_path):
    from kino.admin import run

    cfg = _cfg(tmp_path, admin_chat_id="")
    assert run(cfg, once=True) == 1
