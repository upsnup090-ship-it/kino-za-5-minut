"""Тесты рендера экрана «Прогоны» и извлечения оценки судьи.

Экран читает одну таблицу `runs` (задача #69): исход, формат, длина подписи,
оценка, признак ручного запуска. `_quality_of` — мост от `final_data`
к `runs.quality_score` (задача #72 для ручных прогонов).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kino.admin import _format_run_line, screen_runs
from kino.config import Config, Settings
from kino.pipeline import _quality_of
from kino.schedule_db import RunRecord, ScheduleDb


def _cfg(tmp_path: Path) -> Config:
    return Config(
        sources=[], weights={"news": 1}, bad=[], sad=[],
        settings=Settings(
            schedule_catchup_minutes=30,
            schedule_late_grace_minutes=20,
            timezone="Europe/Moscow",
        ),
        admin_chat_id="-100500",
        state_path=tmp_path / "state.json",
        db_path=tmp_path / "kino.db",
    )


def _rec(**overrides) -> RunRecord:
    base = dict(
        id=1, slot_id=None, slot_at="2026-11-02T11:05",
        started_at="2026-11-02T11:05:00", finished_at="2026-11-02T11:06:00",
        outcome="published", reason="", link=None, format=None, kind=None,
    )
    base.update(overrides)
    return RunRecord(**base)


# --- строка журнала ----------------------------------------------------------


def test_line_has_time_and_outcome():
    line = _format_run_line(_rec())

    assert "2026-11-02 11:05" in line
    assert "published" in line


def test_line_marks_manual_run():
    line = _format_run_line(_rec(is_manual=True))

    assert "✋" in line


def test_line_does_not_mark_scheduled_run():
    assert "✋" not in _format_run_line(_rec(is_manual=False))


def test_line_shows_format_and_length():
    line = _format_run_line(_rec(format="news", caption_len=849))

    assert "news" in line
    assert "849 симв." in line


def test_line_shows_quality():
    line = _format_run_line(_rec(quality_score=8.0))

    assert "оценка 8" in line


def test_line_shows_fractional_quality():
    line = _format_run_line(_rec(quality_score=7.5))

    assert "оценка 7.5" in line


def test_line_omits_missing_quality():
    """None — это «не оценено», а не «оценка 0»."""
    line = _format_run_line(_rec(quality_score=None))

    assert "оценка" not in line


def test_line_omits_zero_length():
    line = _format_run_line(_rec(caption_len=None))

    assert "симв." not in line


def test_line_appends_reason():
    line = _format_run_line(_rec(reason="опоздание 5 мин"))

    assert line.endswith("опоздание 5 мин")


def test_line_without_reason_has_no_dash():
    line = _format_run_line(_rec(reason=""))

    assert "—" not in line


# --- экран -------------------------------------------------------------------


def test_screen_empty_journal(tmp_path):
    reply = screen_runs(_cfg(tmp_path))

    assert "пуст" in reply.text


def test_screen_renders_all_rows(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    for stamp in ("2026-11-02T11:05", "2026-11-02T20:50"):
        run_id = db.start_run(slot_id=None, slot_at=stamp)
        db.finish_run(run_id, outcome="published", format="news", caption_len=500)

    reply = screen_runs(cfg)

    assert "2026-11-02 11:05" in reply.text
    assert "2026-11-02 20:50" in reply.text
    assert reply.text.count("news") == 2


def test_screen_shows_manual_and_scheduled(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.add_slot(0, "11:05")
    slot = db.list_slots()[0]
    a = db.start_run(slot_id=slot.id, slot_at="2026-11-02T11:05")
    db.finish_run(a, outcome="published")
    b = db.start_run(slot_id=None, slot_at="2026-11-02T14:30",
                     is_manual=True, manual_reason="кнопка: финальный пост")
    db.finish_run(b, outcome="published", reason="кнопка")

    reply = screen_runs(cfg)

    assert "✋" in reply.text
    assert "кнопка" in reply.text


def test_screen_has_navigation_buttons(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    run_id = db.start_run(slot_id=None, slot_at="2026-11-02T11:05")
    db.finish_run(run_id, outcome="published")

    reply = screen_runs(cfg)
    data = [btn["callback_data"] for row in reply.keyboard for btn in row]

    # Колбэки S0-схемы: nav:schedule (расписание) и nav:home (главный экран).
    assert "nav:schedule" in data
    assert "nav:home" in data


# --- _quality_of -------------------------------------------------------------


def test_quality_from_direct_field():
    assert _quality_of({"quality": 9}) == 9.0


def test_quality_from_analysis():
    data = {"analysis": {"judge_feedback": {"quality_score": 6}}}
    assert _quality_of(data) == 6.0


def test_quality_direct_wins_over_analysis():
    data = {"quality": 9, "analysis": {"judge_feedback": {"quality_score": 5}}}
    assert _quality_of(data) == 9.0


def test_quality_missing_is_none():
    assert _quality_of({}) is None


def test_quality_out_of_range_is_none():
    assert _quality_of({"quality": 0}) is None
    assert _quality_of({"quality": 11}) is None


def test_quality_garbage_is_none():
    assert _quality_of({"quality": "отлично"}) is None
    assert _quality_of({"quality": None}) is None


def test_quality_string_number_converted():
    assert _quality_of({"quality": "8"}) == 8.0
