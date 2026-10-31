"""Тесты S0: compact/full, группа «Действия», пауза, «Итог за вчера».

Задачи #74 (один экран с двумя режимами рендера), #77 (пауза только в группе
«Действия»), #78 (`nav:today` означает ровно «вчера»), #75 (режим цикла
в блоке состояния).

Ключевое требование: compact и full — не два экрана, а два набора блоков
одного экрана. Логика входа одна, меняется только состав видимого.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from kino.admin import (
    handle_callback,
    screen_home,
    screen_reserve,
    screen_today,
)
from kino.config import Config, Settings
from kino.schedule_db import (
    META_SERVE_MODE,
    META_SERVE_STARTED,
    ScheduleDb,
)


def _cfg(tmp_path: Path) -> Config:
    return Config(
        sources=[], weights={"news": 1}, bad=[], sad=[],
        settings=Settings(
            schedule_mode="slots",
            schedule_catchup_minutes=30,
            schedule_late_grace_minutes=20,
            timezone="Europe/Moscow",
        ),
        admin_chat_id="-100500",
        state_path=tmp_path / "state.json",
        db_path=tmp_path / "kino.db",
    )


def _now() -> datetime:
    return datetime(2026, 11, 2, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))


def _data(reply) -> list[str]:
    return [btn["callback_data"] for row in reply.keyboard for btn in row]


# --- #75: режим цикла в состоянии --------------------------------------------


def test_home_says_cycle_not_running(tmp_path):
    reply = screen_home(_cfg(tmp_path))

    assert "цикл: не запущен" in reply.text


def test_home_says_prod(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.set_meta(META_SERVE_MODE, "prod")
    db.set_meta(META_SERVE_STARTED, "2026-11-02T09:00:00+03:00")

    reply = screen_home(cfg)

    assert "цикл: боевой" in reply.text


def test_home_says_dry_run(tmp_path):
    """Тестовый запуск не должен выглядеть боевым (задача #75)."""
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).set_meta(META_SERVE_MODE, "dry-run")

    reply = screen_home(cfg)

    assert "dry-run" in reply.text
    assert "тестовый" in reply.text


def test_home_shows_last_run(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    run_id = db.start_run(slot_id=None, slot_at="2026-11-02T09:00")
    db.finish_run(run_id, outcome="published", format="news", quality_score=8.0)

    reply = screen_home(cfg)

    assert "последний:" in reply.text
    assert "published" in reply.text
    assert "news" in reply.text
    assert "q8" in reply.text


def test_home_shows_manual_mark(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    run_id = db.start_run(slot_id=None, slot_at="2026-11-02T09:00",
                          is_manual=True, manual_reason="кнопка")
    db.finish_run(run_id, outcome="published")

    assert "✋" in screen_home(cfg).text


# --- #74: два режима рендера -------------------------------------------------


def test_compact_has_no_dangerous_buttons(tmp_path):
    reply = screen_home(_cfg(tmp_path), view="compact")
    data = _data(reply)

    assert "state:pause" not in data
    assert "nav:full" in data


def test_full_has_pause_in_actions_group(tmp_path):
    reply = screen_home(_cfg(tmp_path), view="full")
    data = _data(reply)

    assert "state:pause" in data


def test_full_has_more_blocks_than_compact(tmp_path):
    cfg = _cfg(tmp_path)
    compact = screen_home(cfg, view="compact")
    full = screen_home(cfg, view="full")

    assert len(full.text) > len(compact.text)
    assert len(_data(full)) > len(_data(compact))


def test_full_shows_timezone_and_slots(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).seed_defaults()

    reply = screen_home(cfg, view="full")

    assert "Europe/Moscow" in reply.text
    assert "слотов в неделю" in reply.text


def test_full_has_collapse_button(tmp_path):
    assert "nav:compact" in _data(screen_home(_cfg(tmp_path), view="full"))


def test_nav_full_switches_view(tmp_path):
    cfg = _cfg(tmp_path)

    assert "nav:compact" in _data(handle_callback(cfg, "nav:full", _now()))


def test_nav_compact_switches_back(tmp_path):
    cfg = _cfg(tmp_path)

    assert "nav:full" in _data(handle_callback(cfg, "nav:compact", _now()))


def test_unknown_nav_returns_home(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "nav:nonsense", _now())

    assert "Кино за 5 минут" in reply.text


# --- #77: пауза --------------------------------------------------------------


def test_pause_button_changes_label(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.set_paused(True)

    reply = screen_home(cfg, view="full")
    labels = [btn["text"] for row in reply.keyboard for btn in row]

    assert any("Возобновить" in label for label in labels)


def test_pause_toggles_state(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    assert db.is_paused() is False

    handle_callback(cfg, "state:pause", _now())
    assert db.is_paused() is True

    handle_callback(cfg, "state:pause", _now())
    assert db.is_paused() is False


def test_pause_shows_warning_on_home(tmp_path):
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).set_paused(True)

    assert "ПАУЗЕ" in screen_home(cfg).text


def test_pause_reply_explains_not_skipping(tmp_path):
    """Пауза не съедает слот — это должно быть сказано в ответе."""
    reply = handle_callback(_cfg(tmp_path), "state:pause", _now())

    assert "не пропускаются" in reply.text


def test_pause_is_idempotent_across_processes(tmp_path):
    """Флаг в БД, а не в памяти: второй процесс видит ту же паузу."""
    cfg = _cfg(tmp_path)
    ScheduleDb(cfg.db_path).set_paused(True)

    assert ScheduleDb(cfg.db_path).is_paused() is True


# --- #78: nav:today ----------------------------------------------------------


def test_nav_today_means_yesterday(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "nav:today", _now())

    assert "Итог за 01.11" in reply.text


def test_today_empty_is_honest(tmp_path):
    reply = screen_today(_cfg(tmp_path), _now())

    assert "записей нет" in reply.text


def test_today_counts_published(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    run_id = db.start_run(slot_id=None, slot_at="2026-11-01T11:05")
    db.finish_run(run_id, outcome="published", format="news",
                  caption_len=600, quality_score=8.0)

    reply = screen_today(cfg, _now())

    assert "опубликовано: 1" in reply.text
    assert "средняя оценка: 8.0" in reply.text
    assert "news" in reply.text


def test_today_ignores_other_days(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    run_id = db.start_run(slot_id=None, slot_at="2026-10-30T11:05")
    db.finish_run(run_id, outcome="published")

    assert "записей нет" in screen_today(cfg, _now()).text


def test_today_counts_skipped_and_failed(tmp_path):
    cfg = _cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    a = db.start_run(slot_id=None, slot_at="2026-11-01T11:05")
    db.finish_run(a, outcome="skipped", reason="резерв пуст")
    b = db.start_run(slot_id=None, slot_at="2026-11-01T20:50")
    db.finish_run(b, outcome="failed", reason="сбой")

    reply = screen_today(cfg, _now())

    assert "пропущено: 1" in reply.text
    assert "сбоев: 1" in reply.text


# --- резерв ------------------------------------------------------------------


def test_reserve_screen_shows_counts(tmp_path):
    reply = screen_reserve(_cfg(tmp_path))

    assert "доступно: 0 из 0" in reply.text


def test_nav_reserve_opens_screen(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "nav:reserve", _now())

    assert "Резерв" in reply.text


# --- старые колбэки не сломаны -----------------------------------------------


def test_old_schedule_callback_still_works(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "schedule", _now())

    assert "Расписание" in reply.text


def test_old_home_callback_still_works(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "home", _now())

    assert "Кино за 5 минут" in reply.text
