"""Тесты БД расписания (шаг 7): слоты, резерв, журнал прогонов.

Каждый тест работает на своей временной БД (`tmp_path`), боевой
`data/kino.db` не трогается. Логика планировщика здесь не проверяется —
это шаг 8.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from kino.schedule_db import (
    DEFAULT_SLOTS,
    OUTCOMES,
    WEEKDAY_NAMES,
    ReserveItem,
    ScheduleDb,
    ScheduleDbError,
    Slot,
    format_week,
    normalize_time,
    normalize_weekday,
)


@pytest.fixture
def db(tmp_path: Path) -> ScheduleDb:
    return ScheduleDb(tmp_path / "kino.db")


# --- нормализация входа ------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("9:05", "09:05"), (" 11:05 ", "11:05"), ("23:40", "23:40"), ("00:00", "00:00")],
)
def test_normalize_time_accepts_and_pads(raw, expected):
    assert normalize_time(raw) == expected


@pytest.mark.parametrize("bad", ["", "25:00", "11:60", "11", "11:5:5", "ab:cd", "11-05", None])
def test_normalize_time_rejects_garbage(bad):
    with pytest.raises(ScheduleDbError):
        normalize_time(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, 0), (6, 6), ("пн", 0), ("вс", 6), ("5", 5), (" СБ ", 5)],
)
def test_normalize_weekday_accepts_number_and_name(raw, expected):
    assert normalize_weekday(raw) == expected


@pytest.mark.parametrize("bad", [7, -1, "понедельник", "8", 1.5, True])
def test_normalize_weekday_rejects_garbage(bad):
    with pytest.raises(ScheduleDbError):
        normalize_weekday(bad)  # type: ignore[arg-type]


# --- схема -------------------------------------------------------------------


def test_schema_created_on_first_use(db: ScheduleDb):
    assert not db.path.exists()
    db.ensure_schema()
    assert db.path.exists()
    with sqlite3.connect(db.path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"schedule_slots", "reserve", "runs"} <= tables


def test_schema_is_idempotent(db: ScheduleDb):
    db.ensure_schema()
    db.seed_defaults()
    db.ensure_schema()  # второй раз не должен падать и не должен чистить данные
    assert db.count_slots() == len(DEFAULT_SLOTS)


def test_import_does_not_touch_disk(tmp_path: Path):
    """Импорт модуля не создаёт файл: иначе `kino draft` плодит БД."""
    target = tmp_path / "nested" / "kino.db"
    ScheduleDb(target)
    assert not target.exists()


def test_bad_path_raises_schedule_db_error(tmp_path: Path):
    blocker = tmp_path / "blocker"
    blocker.write_text("не каталог", encoding="utf-8")
    db = ScheduleDb(blocker / "kino.db")
    with pytest.raises(ScheduleDbError):
        db.list_slots()


# --- слоты -------------------------------------------------------------------


def test_seed_fills_owner_grid(db: ScheduleDb):
    added = db.seed_defaults()
    assert added == 13
    slots = db.list_slots()
    assert len(slots) == 13
    assert all(s.enabled for s in slots)

    by_day: dict[int, list[str]] = {}
    for slot in slots:
        by_day.setdefault(slot.weekday, []).append(slot.time)
    assert by_day == {
        0: ["11:05", "20:50"],
        1: ["21:20"],
        2: ["11:05", "20:50"],
        3: ["21:20"],
        4: ["11:05", "20:50", "23:40"],
        5: ["16:40", "22:00"],
        6: ["12:00", "19:50"],
    }


def test_seed_does_not_duplicate(db: ScheduleDb):
    db.seed_defaults()
    added = db.seed_defaults()
    assert added == 0
    assert db.count_slots() == 13


def test_seed_does_not_restore_deleted_slot(db: ScheduleDb):
    db.seed_defaults()
    assert db.delete_slot(4, "23:40") is True
    db.seed_defaults()
    assert db.count_slots() == 12
    assert db.get_slot(4, "23:40") is None


def test_unique_weekday_time(db: ScheduleDb):
    db.add_slot(0, "11:05")
    with pytest.raises(ScheduleDbError, match="уже существует"):
        db.add_slot(0, "11:05")


def test_same_time_different_weekday_allowed(db: ScheduleDb):
    db.add_slot(0, "11:05")
    db.add_slot(2, "11:05")
    assert db.count_slots() == 2


def test_disabled_slot_still_conflicts(db: ScheduleDb):
    """enabled не входит в уникальность: выключенный + включённый в одну
    минуту — конфликт конфигурации, а не два поста."""
    db.add_slot(0, "11:05", enabled=False)
    with pytest.raises(ScheduleDbError):
        db.add_slot(0, "11:05")


def test_list_slots_enabled_only(db: ScheduleDb):
    db.seed_defaults()
    db.set_enabled(1, "21:20", False)
    assert len(db.list_slots()) == 13
    assert len(db.list_slots(enabled_only=True)) == 12
    disabled = db.get_slot(1, "21:20")
    assert disabled is not None and disabled.enabled is False


def test_set_enabled_round_trip(db: ScheduleDb):
    db.add_slot("пт", "23:40")
    assert db.set_enabled("пт", "23:40", False).enabled is False
    slot = db.get_slot(4, "23:40")
    assert slot is not None and slot.enabled is False
    assert db.set_enabled("пт", "23:40", True).enabled is True


def test_set_enabled_missing_slot_raises(db: ScheduleDb):
    with pytest.raises(ScheduleDbError, match="нет"):
        db.set_enabled(0, "11:05", False)


def test_delete_missing_slot_returns_false(db: ScheduleDb):
    assert db.delete_slot(0, "11:05") is False


def test_clear_weekday(db: ScheduleDb):
    db.seed_defaults()
    assert db.clear_weekday("пт") == 3
    assert db.count_slots() == 10
    assert db.clear_weekday("пт") == 0


def test_slots_sorted_by_day_and_time(db: ScheduleDb):
    db.add_slot(6, "19:50")
    db.add_slot(0, "20:50")
    db.add_slot(0, "11:05")
    assert [(s.weekday, s.time) for s in db.list_slots()] == [
        (0, "11:05"),
        (0, "20:50"),
        (6, "19:50"),
    ]


def test_slot_str_shows_disabled_mark(db: ScheduleDb):
    slot = db.add_slot(4, "23:40", enabled=False)
    assert str(slot) == "пт 23:40 (выкл)"
    assert slot.weekday_name == "пт"


def test_format_week_renders_all_days(db: ScheduleDb):
    db.seed_defaults()
    text = format_week(db.list_slots())
    lines = text.splitlines()
    assert len(lines) == 7
    assert lines[0] == "пн: 11:05, 20:50"
    assert lines[4] == "пт: 11:05, 20:50, 23:40"


def test_format_week_empty_day():
    assert format_week([]) == "(пусто)"
    rendered = format_week([Slot(id=1, weekday=0, time="11:05", enabled=True)])
    assert rendered.splitlines()[1] == "вт: —"


def test_weekday_names_cover_seven_days():
    assert len(WEEKDAY_NAMES) == 7
    assert WEEKDAY_NAMES[0] == "пн" and WEEKDAY_NAMES[6] == "вс"


# --- резерв ------------------------------------------------------------------


def test_add_reserve_and_list(db: ScheduleDb):
    rid = db.add_reserve(link="https://a/1", title="Заголовок", format="news", caption="Подпись")
    assert rid is not None
    items = db.list_reserve()
    assert len(items) == 1
    assert items[0].link == "https://a/1"
    assert items[0].title == "Заголовок"
    assert items[0].used_at is None
    assert not items[0].is_expired


def test_reserve_rejects_duplicate_link(db: ScheduleDb):
    assert db.add_reserve(link="https://a/1") is not None
    assert db.add_reserve(link="https://a/1") is None
    assert len(db.list_reserve()) == 1


def test_reserve_respects_max_items(db: ScheduleDb):
    for i in range(5):
        assert db.add_reserve(link=f"https://a/{i}", max_items=5) is not None
    assert db.add_reserve(link="https://a/5", max_items=5) is None
    assert len(db.list_reserve()) == 5


def test_reserve_max_items_counts_only_unused(db: ScheduleDb):
    for i in range(5):
        db.add_reserve(link=f"https://a/{i}", max_items=5)
    db.take_reserve()  # один использован
    assert db.add_reserve(link="https://a/new", max_items=5) is not None


def test_take_reserve_returns_oldest(db: ScheduleDb):
    db.add_reserve(link="https://a/1", title="первый")
    db.add_reserve(link="https://a/2", title="второй")
    taken = db.take_reserve()
    assert taken is not None
    assert taken.link == "https://a/1"
    assert taken.used_at is not None


def test_take_reserve_marks_used_and_does_not_repeat(db: ScheduleDb):
    db.add_reserve(link="https://a/1")
    first = db.take_reserve()
    second = db.take_reserve()
    assert first is not None
    assert second is None
    assert db.list_reserve()[0].used_at is not None


def test_take_reserve_empty_returns_none(db: ScheduleDb):
    assert db.take_reserve() is None


def test_expired_reserve_not_taken(db: ScheduleDb):
    db.add_reserve(link="https://a/old", ttl_hours=1)
    # Состариваем запись: expires_at в прошлом.
    past = (datetime.now().astimezone() - timedelta(hours=2)).isoformat(timespec="seconds")
    with sqlite3.connect(db.path) as conn:
        conn.execute("UPDATE reserve SET expires_at = ?", (past,))
    assert db.take_reserve() is None
    assert db.list_reserve(only_available=True) == []
    assert len(db.list_reserve()) == 1  # не удалён, просто недоступен


def test_expired_flag_on_item(db: ScheduleDb):
    db.add_reserve(link="https://a/old", ttl_hours=1)
    past = (datetime.now().astimezone() - timedelta(hours=2)).isoformat(timespec="seconds")
    with sqlite3.connect(db.path) as conn:
        conn.execute("UPDATE reserve SET expires_at = ?", (past,))
    item = db.list_reserve()[0]
    assert isinstance(item, ReserveItem)
    assert item.is_expired


def test_purge_reserve_removes_used_and_expired(db: ScheduleDb):
    db.add_reserve(link="https://a/used")
    db.add_reserve(link="https://a/expired", ttl_hours=1)
    db.add_reserve(link="https://a/fresh")
    db.take_reserve()  # https://a/used → used_at
    past = (datetime.now().astimezone() - timedelta(hours=2)).isoformat(timespec="seconds")
    with sqlite3.connect(db.path) as conn:
        conn.execute("UPDATE reserve SET expires_at = ? WHERE link = 'https://a/expired'", (past,))
    assert db.purge_reserve() == 2
    assert [i.link for i in db.list_reserve()] == ["https://a/fresh"]


def test_reserve_validates_input(db: ScheduleDb):
    with pytest.raises(ScheduleDbError, match="пустая ссылка"):
        db.add_reserve(link="")
    with pytest.raises(ScheduleDbError, match="ttl_hours"):
        db.add_reserve(link="https://a/1", ttl_hours=0)
    with pytest.raises(ScheduleDbError, match="max_items"):
        db.add_reserve(link="https://a/1", max_items=0)


# --- журнал прогонов ---------------------------------------------------------


def test_start_and_finish_run(db: ScheduleDb):
    slot = db.add_slot(1, "21:20")
    run_id = db.start_run(slot_id=slot.id, slot_at="2026-10-31T21:20:00+03:00")
    db.finish_run(
        run_id,
        outcome="published",
        link="https://a/1",
        format="news",
        kind="photo",
    )
    records = db.recent_runs()
    assert len(records) == 1
    rec = records[0]
    assert rec.slot_id == slot.id
    assert rec.outcome == "published"
    assert rec.link == "https://a/1"
    assert rec.format == "news"
    assert rec.kind == "photo"
    assert rec.finished_at is not None


def test_unfinished_run_is_marked_failed(db: ScheduleDb):
    """Упавший посреди прогона процесс оставляет честную запись, не дыру."""
    db.start_run(slot_id=None, slot_at="2026-10-31T21:20:00+03:00")
    rec = db.recent_runs()[0]
    assert rec.outcome == "failed"
    assert rec.reason == "прогон не завершён"
    assert rec.finished_at is None


def test_finish_run_rejects_unknown_outcome(db: ScheduleDb):
    run_id = db.start_run(slot_id=None, slot_at="2026-10-31T21:20:00+03:00")
    with pytest.raises(ScheduleDbError, match="неизвестный исход"):
        db.finish_run(run_id, outcome="exploded")


def test_all_outcomes_accepted(db: ScheduleDb):
    for outcome in OUTCOMES:
        run_id = db.start_run(slot_id=None, slot_at="2026-10-31T21:20:00+03:00")
        db.finish_run(run_id, outcome=outcome)
    assert {r.outcome for r in db.recent_runs()} == set(OUTCOMES)


def test_slot_done_true_after_closed_run(db: ScheduleDb):
    slot = db.add_slot(1, "21:20")
    at = "2026-10-31T21:20:00+03:00"
    assert db.slot_done(slot.id, at) is False
    run_id = db.start_run(slot_id=slot.id, slot_at=at)
    db.finish_run(run_id, outcome="published")
    assert db.slot_done(slot.id, at) is True


def test_slot_done_false_for_unfinished_run(db: ScheduleDb):
    """Незакрытая запись не блокирует слот: иначе падение съедает слот навсегда."""
    slot = db.add_slot(1, "21:20")
    at = "2026-10-31T21:20:00+03:00"
    db.start_run(slot_id=slot.id, slot_at=at)
    assert db.slot_done(slot.id, at) is False


def test_slot_done_is_per_date(db: ScheduleDb):
    slot = db.add_slot(1, "21:20")
    run_id = db.start_run(slot_id=slot.id, slot_at="2026-10-24T21:20:00+03:00")
    db.finish_run(run_id, outcome="published")
    assert db.slot_done(slot.id, "2026-10-24T21:20:00+03:00") is True
    assert db.slot_done(slot.id, "2026-10-31T21:20:00+03:00") is False


def test_recent_runs_limit_and_order(db: ScheduleDb):
    for i in range(5):
        run_id = db.start_run(slot_id=None, slot_at=f"2026-10-3{i}T21:20:00+03:00")
        db.finish_run(run_id, outcome="skipped", reason=f"причина {i}")
    latest = db.recent_runs(limit=2)
    assert len(latest) == 2
    assert latest[0].reason == "причина 4"


def test_recent_runs_rejects_bad_limit(db: ScheduleDb):
    with pytest.raises(ScheduleDbError):
        db.recent_runs(limit=0)


def test_purge_runs_removes_old(db: ScheduleDb):
    old_id = db.start_run(slot_id=None, slot_at="2026-01-01T21:20:00+03:00")
    db.finish_run(old_id, outcome="published")
    new_id = db.start_run(slot_id=None, slot_at="2026-10-31T21:20:00+03:00")
    db.finish_run(new_id, outcome="published")
    past = (datetime.now().astimezone() - timedelta(days=200)).isoformat(timespec="seconds")
    with sqlite3.connect(db.path) as conn:
        conn.execute("UPDATE runs SET started_at = ? WHERE id = ?", (past, old_id))
    assert db.purge_runs(retention_days=90) == 1
    assert len(db.recent_runs()) == 1


def test_purge_runs_rejects_bad_retention(db: ScheduleDb):
    with pytest.raises(ScheduleDbError):
        db.purge_runs(retention_days=0)


def test_delete_slot_keeps_run_with_null_slot_id(db: ScheduleDb):
    """Слот удалён из админки — история прогонов остаётся, ссылка обнуляется."""
    slot = db.add_slot(1, "21:20")
    run_id = db.start_run(slot_id=slot.id, slot_at="2026-10-31T21:20:00+03:00")
    db.finish_run(run_id, outcome="published", link="https://a/1")
    db.delete_slot(1, "21:20")
    rec = db.recent_runs()[0]
    assert rec.slot_id is None
    assert rec.link == "https://a/1"


# --- изоляция от боевого файла ----------------------------------------------


def test_two_dbs_do_not_share_state(tmp_path: Path):
    first = ScheduleDb(tmp_path / "a.db")
    second = ScheduleDb(tmp_path / "b.db")
    first.seed_defaults()
    assert first.count_slots() == 13
    assert second.count_slots() == 0