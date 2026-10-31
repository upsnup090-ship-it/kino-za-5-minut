"""Тесты режима слотов (шаг 8): наступление слота, опоздание, пропуск.

Логика чистая — время параметром, поэтому тесты не спят. Цикл serve
проверяется отдельно, с моком pipeline.run и временной БД.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kino.config import Config, Settings, Source
from kino.schedule import (
    ScheduleError,
    SlotConfig,
    SlotOccurrence,
    describe_slots,
    due_slot,
    is_late,
    lateness_minutes,
    missed_slots,
    next_occurrence,
    slot_datetime,
)
from kino.schedule_db import ScheduleDb


def _cfg(**kwargs) -> SlotConfig:
    params = {"catchup_minutes": 30, "late_grace_minutes": 20, "tz": "Europe/Moscow"}
    params.update(kwargs)
    return SlotConfig(**params)


def _at(hour: int, minute: int, day: int = 31, month: int = 10) -> datetime:
    """Момент в локальной зоне; 31.10.2026 — суббота (weekday 5)."""
    return datetime(2026, month, day, hour, minute, tzinfo=_cfg().zone)


# --- конфиг ------------------------------------------------------------------


def test_slot_config_rejects_bad_catchup():
    with pytest.raises(ScheduleError, match="catchup_minutes"):
        SlotConfig(catchup_minutes=0)


def test_slot_config_rejects_negative_grace():
    with pytest.raises(ScheduleError, match="late_grace_minutes"):
        SlotConfig(late_grace_minutes=-1)


def test_slot_config_rejects_unknown_timezone():
    with pytest.raises(ScheduleError, match="неизвестная зона"):
        _ = SlotConfig(tz="Mars/Olympus").zone


def test_slot_config_local_now_naive_is_local():
    moment = datetime(2026, 10, 31, 12, 0)
    assert _cfg().local_now(moment).tzinfo is not None


# --- slot_datetime -----------------------------------------------------------


def test_slot_datetime_same_weekday():
    friday = _at(12, 0, day=30)
    moment = slot_datetime(4, "23:40", friday)
    assert moment.strftime("%Y-%m-%d %H:%M") == "2026-10-30 23:40"
    assert moment.weekday() == 4


def test_slot_datetime_rolls_forward_to_next_weekday():
    saturday = _at(12, 0)
    moment = slot_datetime(0, "11:05", saturday)  # ближайший понедельник
    assert moment.strftime("%Y-%m-%d %H:%M") == "2026-11-02 11:05"
    assert moment.weekday() == 0


def test_slot_datetime_rejects_garbage_time():
    with pytest.raises(ScheduleError):
        slot_datetime(0, "25:00", _at(12, 0))


# --- due_slot ----------------------------------------------------------------


def test_due_slot_exact_minute():
    slots = [(1, 5, "16:40")]
    occ = due_slot(_at(16, 40), slots, _cfg())
    assert occ is not None
    assert occ.slot_at == "2026-10-31T16:40"
    assert occ.slot_id == 1


def test_due_slot_late_tick_still_catches():
    """Тик пришёлся на 16:40:50 — слот не потерян (главный дефект шага 8)."""
    slots = [(1, 5, "16:40")]
    occ = due_slot(_at(16, 40) + timedelta(seconds=50), slots, _cfg())
    assert occ is not None


def test_due_slot_catches_before_end_of_window():
    slots = [(1, 5, "16:40")]
    occ = due_slot(_at(17, 9), slots, _cfg(catchup_minutes=30))
    assert occ is not None


def test_due_slot_outside_window_is_none():
    slots = [(1, 5, "16:40")]
    assert due_slot(_at(17, 10), slots, _cfg(catchup_minutes=30)) is None


def test_due_slot_before_slot_is_none():
    slots = [(1, 5, "16:40")]
    assert due_slot(_at(16, 39), slots, _cfg()) is None


def test_due_slot_skips_done():
    slots = [(1, 5, "16:40")]
    done = {"2026-10-31T16:40"}
    assert due_slot(_at(16, 45), slots, _cfg(), done) is None


def test_due_slot_wrong_weekday_is_none():
    slots = [(1, 4, "16:40")]  # пятница, а сейчас суббота
    assert due_slot(_at(16, 45), slots, _cfg()) is None


def test_due_slot_after_midnight_catches_previous_day():
    """Слот пт 23:40 с окном 30 мин попадает в субботу 00:09."""
    slots = [(7, 4, "23:40")]
    occ = due_slot(_at(0, 9), slots, _cfg())
    assert occ is not None
    assert occ.slot_at == "2026-10-30T23:40"


def test_due_slot_after_midnight_window_is_half_open():
    """00:10 — ровно slot + catchup, окно полуоткрытое: уже не попадает."""
    slots = [(7, 4, "23:40")]
    assert due_slot(_at(0, 10), slots, _cfg()) is None


def test_due_slot_after_midnight_outside_window():
    slots = [(7, 4, "23:40")]
    assert due_slot(_at(0, 15), slots, _cfg()) is None


def test_due_slot_returns_earliest_of_several():
    slots = [(2, 5, "16:40"), (1, 5, "16:40")]
    occ = due_slot(_at(16, 45), slots, _cfg())
    assert occ is not None
    assert occ.slot_id == 1  # при равном времени решает id


def test_due_slot_empty_slots():
    assert due_slot(_at(12, 0), [], _cfg()) is None


# --- missed_slots ------------------------------------------------------------


def test_missed_slot_detected_after_window():
    slots = [(1, 5, "16:40")]
    missed = missed_slots(_at(18, 0), slots, _cfg())
    assert [m.slot_at for m in missed] == ["2026-10-31T16:40"]


def test_missed_slot_not_reported_inside_window():
    slots = [(1, 5, "16:40")]
    assert missed_slots(_at(16, 50), slots, _cfg()) == []


def test_missed_slot_not_reported_when_done():
    slots = [(1, 5, "16:40")]
    done = {"2026-10-31T16:40"}
    assert missed_slots(_at(18, 0), slots, _cfg(), done) == []


def test_missed_slot_respects_lookback():
    slots = [(1, 5, "16:40")]
    missed = missed_slots(_at(18, 0, day=31), slots, _cfg(), lookback_hours=24)
    assert [m.slot_at for m in missed] == ["2026-10-31T16:40"]


def test_missed_slots_sorted():
    slots = [(1, 5, "16:40"), (2, 5, "22:00")]
    missed = missed_slots(_at(23, 0), slots, _cfg())
    assert [m.slot_at for m in missed] == ["2026-10-31T16:40", "2026-10-31T22:00"]


# --- next_occurrence ---------------------------------------------------------


def test_next_occurrence_today():
    slots = [(1, 5, "22:00")]
    upcoming = next_occurrence(_at(18, 0), slots, _cfg())
    assert upcoming is not None
    assert upcoming.slot_at == "2026-10-31T22:00"


def test_next_occurrence_tomorrow():
    slots = [(1, 6, "12:00")]
    upcoming = next_occurrence(_at(23, 0), slots, _cfg())
    assert upcoming is not None
    assert upcoming.slot_at == "2026-11-01T12:00"


def test_next_occurrence_skips_current_minute():
    """Строго после now: слот, который уже наступил, не «следующий»."""
    slots = [(1, 5, "22:00")]
    upcoming = next_occurrence(_at(22, 0), slots, _cfg())
    assert upcoming is not None
    assert upcoming.slot_at == "2026-11-07T22:00"


def test_next_occurrence_empty():
    assert next_occurrence(_at(12, 0), [], _cfg()) is None


# --- опоздание ---------------------------------------------------------------


def test_lateness_zero_at_slot():
    occ = due_slot(_at(22, 0), [(1, 5, "22:00")], _cfg())
    assert occ is not None
    assert lateness_minutes(occ, _at(22, 0), _cfg()) == 0


def test_lateness_counts_minutes():
    occ = due_slot(_at(22, 0), [(1, 5, "22:00")], _cfg())
    assert occ is not None
    assert lateness_minutes(occ, _at(22, 7), _cfg()) == 7


def test_is_late_within_grace():
    occ = due_slot(_at(22, 0), [(1, 5, "22:00")], _cfg(late_grace_minutes=20))
    assert occ is not None
    assert is_late(occ, _at(22, 20), _cfg(late_grace_minutes=20)) is False


def test_is_late_past_grace():
    occ = due_slot(_at(22, 0), [(1, 5, "22:00")], _cfg(late_grace_minutes=20))
    assert occ is not None
    assert is_late(occ, _at(22, 21), _cfg(late_grace_minutes=20)) is True


def test_occurrence_str_and_when():
    occ = SlotOccurrence(slot_id=1, weekday=5, time="22:00",
                         date="2026-10-31", slot_at="2026-10-31T22:00")
    assert str(occ) == "2026-10-31 22:00"
    assert occ.when.hour == 22


def test_describe_slots_renders_grid():
    text = describe_slots([(1, 0, "11:05"), (2, 0, "20:50"), (3, 4, "23:40")])
    assert text.startswith("пн 11:05/20:50")
    assert "пт 23:40" in text
    assert "вт —" in text


def test_describe_slots_empty():
    assert describe_slots([]) == "(слотов нет)"


# --- цикл serve в режиме слотов ---------------------------------------------


def _serve_cfg(tmp_path: Path, **settings_kwargs) -> Config:
    params = dict(
        schedule_mode="slots",
        schedule_windows=["11:00-18:00"],
        schedule_catchup_minutes=30,
        schedule_late_grace_minutes=20,
        timezone="Europe/Moscow",
        tick_seconds=60,
        log_retention_days=14,
        log_max_total_bytes=1024 * 1024,
        log_purge_interval_hours=24,
        log_file=str(tmp_path / "logs" / "kino.log"),
    )
    params.update(settings_kwargs)
    return Config(
        sources=[Source(name="test", url="http://example.com/feed")],
        weights={"facts": 1},
        bad=[],
        sad=[],
        settings=Settings(**params),
        llm_base_url="http://llm.test",
        llm_api_key="k",
        draft_model="d",
        judge_model="j",
        bot_token="b",
        channel_chat_id="-100",
        admin_chat_id="",
        state_path=tmp_path / "state.json",
        db_path=tmp_path / "kino.db",
    )


def _result(**kwargs) -> SimpleNamespace:
    base = dict(
        published=True, aborted=False, abort_reason="", verdict="accept",
        kind="photo", requested_kind="photo", format="news", link="https://a/1",
        caption="", image_url="", dry_run=False,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _freeze(module: str, moment: datetime):
    """Подменяет datetime.now в модуле, не ломая datetime.fromisoformat."""

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment

    return patch(f"{module}.datetime", _DT)


def test_serve_slots_publishes_due_slot(tmp_path):
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.add_slot(5, "16:40")

    with patch("kino.serve.run", return_value=_result()) as mock_run, \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(16, 41)):
        assert serve(cfg, once=True) == 0

    mock_run.assert_called_once()
    runs = db.recent_runs()
    assert len(runs) == 1
    assert runs[0].outcome == "published"
    assert runs[0].link == "https://a/1"
    assert runs[0].format == "news"
    assert runs[0].reason == ""


def test_serve_slots_does_not_repeat_done_slot(tmp_path):
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    slot = db.add_slot(5, "16:40")
    run_id = db.start_run(slot_id=slot.id, slot_at="2026-10-31T16:40")
    db.finish_run(run_id, outcome="published")

    with patch("kino.serve.run") as mock_run, \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(16, 45)):
        assert serve(cfg, once=True) == 0

    mock_run.assert_not_called()


def test_serve_slots_marks_late_publication(tmp_path):
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path, schedule_catchup_minutes=60, schedule_late_grace_minutes=20)
    db = ScheduleDb(cfg.db_path)
    db.add_slot(5, "16:40")

    with patch("kino.serve.run", return_value=_result()), \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(17, 5)):
        assert serve(cfg, once=True) == 0

    rec = db.recent_runs()[0]
    assert rec.outcome == "published"
    assert rec.reason == "опоздание 25 мин"


def test_serve_slots_marks_missed_slot_skipped(tmp_path):
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.add_slot(5, "16:40")

    with patch("kino.serve.run") as mock_run, \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(18, 0)):
        assert serve(cfg, once=True) == 0

    mock_run.assert_not_called()
    runs = db.recent_runs()
    assert len(runs) == 1
    assert runs[0].outcome == "skipped"
    assert "опоздание" in runs[0].reason


def test_serve_slots_reject_falls_back_to_skipped_without_reserve(tmp_path):
    """Reject без резерва: слот закрывается `skipped`, а не молчанием."""
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.add_slot(5, "16:40")

    with patch("kino.serve.run", return_value=_result(published=False, verdict="reject")), \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(16, 42)):
        assert serve(cfg, once=True) == 0

    rec = db.recent_runs()[0]
    assert rec.outcome == "skipped"
    assert "судья отбил" in rec.reason
    assert "резерв пуст" in rec.reason


def test_serve_slots_abort_falls_back_to_skipped_without_reserve(tmp_path):
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.add_slot(5, "16:40")

    with patch("kino.serve.run", return_value=_result(
        published=False, aborted=True, abort_reason="дедлайн"
    )), patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(16, 42)):
        assert serve(cfg, once=True) == 0

    rec = db.recent_runs()[0]
    assert rec.outcome == "skipped"
    assert "дедлайн" in rec.reason


def test_serve_slots_reject_uses_reserve(tmp_path):
    """Reject + резерв: слот закрывается старым постом резерва."""
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.add_slot(5, "16:40")
    db.add_reserve(
        link="https://old/1", title="Запасной", format="news",
        caption="Старая подпись", kind="photo",
    )

    with patch("kino.serve.run", return_value=_result(published=False, verdict="reject")), \
         patch("kino.serve._publish_reserve_item") as mock_pub, \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(16, 42)):
        assert serve(cfg, once=True) == 0

    mock_pub.assert_called_once()
    rec = db.recent_runs()[0]
    assert rec.outcome == "reserve"
    assert rec.link == "https://old/1"
    assert rec.format == "news"
    # Взятый пост больше не доступен.
    assert db.take_reserve() is None


def test_serve_slots_abort_uses_reserve(tmp_path):
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.add_slot(5, "16:40")
    db.add_reserve(link="https://old/2", caption="подпись")

    with patch("kino.serve.run", return_value=_result(
        published=False, aborted=True, abort_reason="дедлайн"
    )), patch("kino.serve._publish_reserve_item"), \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(16, 42)):
        assert serve(cfg, once=True) == 0

    rec = db.recent_runs()[0]
    assert rec.outcome == "reserve"
    assert rec.link == "https://old/2"


def test_serve_slots_reserve_dry_run_does_not_publish(tmp_path):
    """В dry-run резерв берётся, но в канал ничего не уходит."""
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.add_slot(5, "16:40")
    db.add_reserve(link="https://old/3", caption="подпись")

    with patch("kino.serve.run", return_value=_result(published=False, verdict="reject")), \
         patch("kino.serve._publish_reserve_item") as mock_pub, \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(16, 42)):
        assert serve(cfg, dry_run=True, once=True) == 0

    mock_pub.assert_not_called()
    assert db.recent_runs()[0].outcome == "reserve"


def test_serve_slots_reserve_skips_expired(tmp_path):
    """Просроченный резерв не берётся: слот пропускается."""
    import sqlite3
    from datetime import datetime as _dt, timedelta as _td

    from kino.serve import serve

    cfg = _serve_cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.add_slot(5, "16:40")
    db.add_reserve(link="https://old/4", caption="подпись", ttl_hours=1)
    past = (_dt.now().astimezone() - _td(hours=2)).isoformat(timespec="seconds")
    with sqlite3.connect(db.path) as conn:
        conn.execute("UPDATE reserve SET expires_at = ?", (past,))

    with patch("kino.serve.run", return_value=_result(published=False, verdict="reject")), \
         patch("kino.serve._publish_reserve_item") as mock_pub, \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(16, 42)):
        assert serve(cfg, once=True) == 0

    mock_pub.assert_not_called()
    assert db.recent_runs()[0].outcome == "skipped"


def test_serve_slots_reserve_empty_notifies_admin(tmp_path):
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.add_slot(5, "16:40")

    with patch("kino.serve.run", return_value=_result(published=False, verdict="reject")), \
         patch("kino.serve._notify_reserve_empty") as mock_notify, \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(16, 42)):
        assert serve(cfg, once=True) == 0

    mock_notify.assert_called_once()


def test_serve_slots_dry_run_records_skipped(tmp_path):
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.add_slot(5, "16:40")

    with patch("kino.serve.run", return_value=_result(published=False, dry_run=True)), \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(16, 42)):
        assert serve(cfg, dry_run=True, once=True) == 0

    rec = db.recent_runs()[0]
    assert rec.outcome == "skipped"
    assert rec.reason == "dry-run"


def test_serve_slots_empty_db_fails_loudly(tmp_path):
    """Пустая БД в режиме slots — ошибка конфигурации, а не тишина."""
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path)
    assert serve(cfg, once=True) == 1


def test_serve_slots_disabled_slot_not_published(tmp_path):
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.add_slot(5, "16:40", enabled=False)

    with patch("kino.serve.run") as mock_run, \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(16, 42)):
        assert serve(cfg, once=True) == 1
    mock_run.assert_not_called()


def test_serve_windows_mode_still_uses_windows(tmp_path):
    """mode=windows идёт старым путём: слоты в БД не участвуют."""
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path, schedule_mode="windows")
    db = ScheduleDb(cfg.db_path)
    db.add_slot(5, "16:40")  # слот есть, но режим windows его игнорирует

    with patch("kino.serve.run", return_value=_result()) as mock_run, \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(12, 0)):
        assert serve(cfg, once=True) == 0

    # 12:00 внутри окна 11:00-18:00 → публикует, хотя слот 16:40 не наступил.
    assert mock_run.call_count == 1


def test_serve_slots_does_not_require_windows(tmp_path):
    """В режиме slots пустой windows — норма, а не ошибка конфигурации.

    Живой дефект: serve строил build_schedule (окна) до проверки режима и
    падал с «windows должен быть непустым списком» при пустом списке.
    """
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path, schedule_windows=[])
    db = ScheduleDb(cfg.db_path)
    db.add_slot(5, "16:40")

    with patch("kino.serve.run", return_value=_result()), \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(16, 42)):
        assert serve(cfg, once=True) == 0

    assert db.recent_runs()[0].outcome == "published"


def test_serve_slots_no_duplicate_skipped_records(tmp_path):
    """Каждый пропущенный слот пишется в runs один раз, а не каждый тик."""
    from kino.serve import serve

    cfg = _serve_cfg(tmp_path)
    db = ScheduleDb(cfg.db_path)
    db.add_slot(5, "16:40")

    with patch("kino.serve.run") as mock_run, \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(18, 0)):
        assert serve(cfg, once=True) == 0

    # Второй тик в то же время не должен дописать ещё одну запись.
    with patch("kino.serve.run") as mock_run, \
         patch("kino.serve.time.sleep"), \
         _freeze("kino.serve", _at(18, 1)):
        assert serve(cfg, once=True) == 0

    mock_run.assert_not_called()
    skipped = [r for r in db.recent_runs() if r.outcome == "skipped"]
    assert len(skipped) == 1
