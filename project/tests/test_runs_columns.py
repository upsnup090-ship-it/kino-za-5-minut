"""Тесты расширенной таблицы `runs`: ручной прогон, длина подписи, оценка.

Задачи #69 и #72. Обе про одно: экран «Прогоны» должен отвечать на вопросы
«что было в 21:20», «почему в 14:30 вышел пост» и «насколько хорош текст»,
читая одну таблицу, а не CSV статистики и не логи.

Отдельно проверяется миграция: боевая БД на сервере создана до появления
этих колонок, и `ensure_schema` обязан досоздать их, не потеряв данные.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kino.schedule_db import ScheduleDb, ScheduleDbError


@pytest.fixture
def db(tmp_path: Path) -> ScheduleDb:
    return ScheduleDb(tmp_path / "kino.db")


# --- схема и миграция --------------------------------------------------------


def test_new_columns_exist(db: ScheduleDb):
    db.ensure_schema()
    with sqlite3.connect(db.path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}

    assert {"is_manual", "manual_reason", "caption_len", "quality_score"} <= columns


def test_migration_adds_columns_to_old_db(tmp_path: Path):
    """Старая БД без новых колонок должна дожить без потери записей."""
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE schedule_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                weekday INTEGER NOT NULL,
                time TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (weekday, time)
            );
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_id INTEGER,
                slot_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                outcome TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                link TEXT,
                format TEXT,
                kind TEXT
            );
            INSERT INTO runs (slot_at, started_at, finished_at, outcome, reason)
            VALUES ('2026-10-31T11:05', '2026-10-31T11:05:00', '2026-10-31T11:06:00',
                    'published', 'старая запись');
            """
        )

    db = ScheduleDb(path)
    db.ensure_schema()
    db.ensure_schema()  # идемпотентность: второй вызов ничего не ломает

    records = db.recent_runs()
    assert len(records) == 1
    assert records[0].outcome == "published"
    assert records[0].reason == "старая запись"
    # Новые колонки у старой записи — умолчания, а не NULL-падение.
    assert records[0].is_manual is False
    assert records[0].manual_reason == ""
    assert records[0].caption_len is None
    assert records[0].quality_score is None


# --- #72: ручной прогон ------------------------------------------------------


def test_manual_run_is_marked(db: ScheduleDb):
    run_id = db.start_run(
        slot_id=None, slot_at="2026-10-31T14:30",
        is_manual=True, manual_reason="кнопка: финальный пост",
    )
    db.finish_run(run_id, outcome="published", reason="кнопка")

    rec = db.recent_runs()[0]
    assert rec.is_manual is True
    assert rec.manual_reason == "кнопка: финальный пост"


def test_scheduled_run_is_not_manual(db: ScheduleDb):
    db.add_slot(0, "11:05")
    slot = db.list_slots()[0]
    run_id = db.start_run(slot_id=slot.id, slot_at="2026-11-02T11:05")
    db.finish_run(run_id, outcome="published")

    rec = db.recent_runs()[0]
    assert rec.is_manual is False
    assert rec.manual_reason == ""


def test_manual_and_scheduled_are_distinguishable(db: ScheduleDb):
    """Оба в одном журнале, но различимы без догадок."""
    db.add_slot(0, "11:05")
    slot = db.list_slots()[0]
    a = db.start_run(slot_id=slot.id, slot_at="2026-11-02T11:05")
    db.finish_run(a, outcome="published")
    b = db.start_run(slot_id=None, slot_at="2026-11-02T14:30",
                     is_manual=True, manual_reason="кнопка: финальный пост")
    db.finish_run(b, outcome="published", reason="кнопка")

    records = db.recent_runs()
    manual = [r for r in records if r.is_manual]
    scheduled = [r for r in records if not r.is_manual]
    assert len(manual) == 1
    assert len(scheduled) == 1
    assert manual[0].manual_reason == "кнопка: финальный пост"


# --- #69: длина подписи и оценка ---------------------------------------------


def test_caption_len_and_quality_saved(db: ScheduleDb):
    run_id = db.start_run(slot_id=None, slot_at="2026-10-31T14:30")
    db.finish_run(
        run_id, outcome="published",
        format="news", kind="photo",
        caption_len=849, quality_score=8.0,
    )

    rec = db.recent_runs()[0]
    assert rec.caption_len == 849
    assert rec.quality_score == 8.0


def test_quality_none_is_not_zero(db: ScheduleDb):
    """Ноль в отчёте выглядел бы как «оценка 0» и тянул среднее вниз."""
    run_id = db.start_run(slot_id=None, slot_at="2026-10-31T14:30")
    db.finish_run(run_id, outcome="skipped", reason="резерв пуст")

    rec = db.recent_runs()[0]
    assert rec.quality_score is None
    assert rec.caption_len is None


def test_quality_fractional_saved(db: ScheduleDb):
    run_id = db.start_run(slot_id=None, slot_at="2026-10-31T14:30")
    db.finish_run(run_id, outcome="published", quality_score=7.5)

    assert db.recent_runs()[0].quality_score == 7.5


def test_finish_run_rejects_unknown_outcome(db: ScheduleDb):
    run_id = db.start_run(slot_id=None, slot_at="2026-10-31T14:30")
    with pytest.raises(ScheduleDbError):
        db.finish_run(run_id, outcome="nonsense")


def test_start_run_keeps_failed_until_finished(db: ScheduleDb):
    """Упавший посреди прогона процесс оставляет честную запись."""
    db.start_run(slot_id=None, slot_at="2026-10-31T14:30")

    rec = db.recent_runs()[0]
    assert rec.outcome == "failed"
    assert rec.reason == "прогон не завершён"
    assert rec.finished_at is None
