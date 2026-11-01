"""Тесты паузы публикаций в цикле serve.

Пауза — это «не публикуй сейчас», а не «пропусти слоты». При снятии слот
возвращается в работу без догонки: иначе пауза на час съела бы слот до конца
недели. Проверяется поведение цикла, а не только флаг в БД.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from kino.config import Config, Settings
from kino.schedule_db import ScheduleDb


def _cfg(tmp_path: Path) -> Config:
    return Config(
        sources=[], weights={"news": 1}, bad=[], sad=[],
        settings=Settings(
            schedule_mode="slots",
            schedule_catchup_minutes=30,
            schedule_late_grace_minutes=20,
            timezone="Europe/Moscow",
            tick_seconds=1,
        ),
        admin_chat_id="-100500",
        state_path=tmp_path / "state.json",
        db_path=tmp_path / "kino.db",
    )


def test_paused_cycle_does_not_publish(tmp_path):
    """На паузе run() не вызывается вовсе."""
    from kino.serve import serve

    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.seed_defaults()
    db.set_paused(True)

    with patch("kino.serve.run") as mocked_run, \
         patch("kino.serve.time.sleep", side_effect=KeyboardInterrupt):
        try:
            serve(cfg, once=False, tick_seconds=1)
        except KeyboardInterrupt:
            pass

    mocked_run.assert_not_called()


def test_paused_cycle_does_not_mark_slot_skipped(tmp_path):
    """Пауза не пишет `skipped` — слот вернётся в работу."""
    from kino.serve import serve

    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.seed_defaults()
    db.set_paused(True)

    with patch("kino.serve.run") as mocked_run, \
         patch("kino.serve.time.sleep", side_effect=KeyboardInterrupt):
        try:
            serve(cfg, once=False, tick_seconds=1)
        except KeyboardInterrupt:
            pass

    runs = db.recent_runs()
    skipped = [r for r in runs if r.outcome == "skipped"]
    assert skipped == []


def test_unpaused_cycle_runs_again(tmp_path):
    """Снятие паузы возвращает цикл в работу."""
    from kino.serve import serve

    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.seed_defaults()
    db.set_paused(False)

    calls = {"n": 0}

    def _stop(*args, **kwargs):
        calls["n"] += 1
        raise KeyboardInterrupt

    with patch("kino.serve.time.sleep", side_effect=_stop):
        try:
            serve(cfg, once=False, tick_seconds=1)
        except KeyboardInterrupt:
            pass

    # Цикл дошёл до sleep — значит пауза не блокировала тик.
    assert calls["n"] >= 1


def test_pause_flag_survives_new_connection(tmp_path):
    """Флаг в БД, а не в памяти процесса."""
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).set_paused(True)

    assert ScheduleDb(cfg.db_path).is_paused() is True


def test_pause_default_is_off(tmp_path):
    cfg = _cfg(tmp_path)

    assert ScheduleDb(cfg.db_path).is_paused() is False


def test_set_paused_writes_zero_not_removes(tmp_path):
    """`set_paused(False)` пишет `0`, а не удаляет ключ."""
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.set_paused(True)
    db.set_paused(False)

    assert db.get_meta("paused") == "0"
    assert db.is_paused() is False
