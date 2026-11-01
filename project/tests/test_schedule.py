"""Тесты расписания: окна, суточный лимит, пауза, точки.

Логика чистая — время передаётся параметром, поэтому тесты не спят и не
зависят от момента запуска.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from kino.schedule import (
    DayState,
    ScheduleConfig,
    ScheduleError,
    Window,
    apply_post,
    describe_day,
    is_window_open,
    next_slot,
    open_window,
    parse_windows,
    should_publish,
)


def _schedule(**kwargs) -> ScheduleConfig:
    """Расписание по умолчанию: окно 11:00-18:00 и точка 22:50."""
    params: dict[str, Any] = {
        "windows": parse_windows(["11:00-18:00", "22:50"]),
        "max_posts_per_day": 3,
        "min_gap_minutes": 45,
        "tz": "Europe/Moscow",
    }
    params.update(kwargs)
    return ScheduleConfig(**params)


def _at(hour: int, minute: int, day: int = 18) -> datetime:
    """Момент в локальной зоне расписания."""
    return datetime(2026, 10, day, hour, minute, tzinfo=_schedule().zone)


# --- разбор окон -------------------------------------------------------------


def test_parse_interval_window():
    windows = parse_windows(["11:00-18:00"])
    assert windows == [Window(start=datetime(2026, 1, 1, 11, 0).time(),
                              end=datetime(2026, 1, 1, 18, 0).time())]


def test_parse_point_window_is_zero_length():
    window = parse_windows(["22:50"])[0]
    assert window.is_point
    assert window.start == window.end


def test_parse_multiple_windows_sorted():
    windows = parse_windows(["22:50", "11:00-18:00"])
    assert [str(w) for w in windows] == ["11:00-18:00", "22:50"]


def test_parse_tolerates_spaces():
    assert parse_windows([" 11:00 - 18:00 "])[0] == parse_windows(["11:00-18:00"])[0]


@pytest.mark.parametrize("bad", ["", "мусор", "25:00", "11:70", "11:00-", "11-18"])
def test_parse_rejects_garbage(bad):
    with pytest.raises(ScheduleError):
        parse_windows([bad])


def test_parse_rejects_empty_list():
    with pytest.raises(ScheduleError):
        parse_windows([])


def test_parse_rejects_zero_length_interval():
    with pytest.raises(ScheduleError, match="нулевой длины"):
        parse_windows(["11:00-11:00"])


def test_parse_rejects_non_string():
    with pytest.raises(ScheduleError):
        parse_windows([123])


# --- попадание в окно --------------------------------------------------------


def test_interval_contains_inside():
    window = parse_windows(["11:00-18:00"])[0]
    assert window.contains(_at(11, 0)) is True
    assert window.contains(_at(14, 30)) is True
    assert window.contains(_at(17, 59)) is True


def test_interval_excludes_bounds():
    window = parse_windows(["11:00-18:00"])[0]
    assert window.contains(_at(10, 59)) is False
    assert window.contains(_at(18, 0)) is False   # конец не входит


def test_point_matches_exact_minute():
    window = parse_windows(["22:50"])[0]
    assert window.contains(_at(22, 50)) is True
    assert window.contains(_at(22, 51)) is False
    assert window.contains(_at(22, 49)) is False


def test_window_across_midnight():
    window = parse_windows(["22:00-02:00"])[0]
    assert window.contains(_at(23, 0)) is True
    assert window.contains(_at(1, 0)) is True
    assert window.contains(_at(3, 0)) is False


def test_open_window_returns_first_match():
    schedule = _schedule()
    assert str(open_window(_at(14, 0), schedule)) == "11:00-18:00"
    assert str(open_window(_at(22, 50), schedule)) == "22:50"
    assert open_window(_at(20, 0), schedule) is None


def test_is_window_open_wrapper():
    schedule = _schedule()
    window = schedule.windows[0]
    assert is_window_open(_at(12, 0), window) is True


# --- конфигурация ------------------------------------------------------------


def test_config_rejects_empty_windows():
    with pytest.raises(ScheduleError):
        ScheduleConfig(windows=[])


def test_config_rejects_bad_limit():
    with pytest.raises(ScheduleError):
        ScheduleConfig(windows=parse_windows(["11:00-18:00"]), max_posts_per_day=0)


def test_config_rejects_negative_gap():
    with pytest.raises(ScheduleError):
        ScheduleConfig(windows=parse_windows(["11:00-18:00"]), min_gap_minutes=-1)


def test_config_rejects_unknown_timezone():
    schedule = _schedule(tz="Несуществующая/Зона")
    with pytest.raises(ScheduleError, match="неизвестная зона"):
        _ = schedule.zone


def test_local_now_treats_naive_as_local():
    schedule = _schedule()
    naive = datetime(2026, 10, 18, 14, 0)
    assert schedule.local_now(naive).hour == 14


def test_local_now_converts_aware():
    schedule = _schedule()
    from datetime import timezone
    utc = datetime(2026, 10, 18, 11, 0, tzinfo=timezone.utc)
    # Москва = UTC+3
    assert schedule.local_now(utc).hour == 14


# --- should_publish ----------------------------------------------------------


def test_publishes_inside_window_with_free_limit():
    schedule = _schedule()
    assert should_publish(_at(14, 0), schedule, DayState()) is True


def test_does_not_publish_outside_windows():
    schedule = _schedule()
    assert should_publish(_at(20, 0), schedule, DayState()) is False
    assert should_publish(_at(3, 0), schedule, DayState()) is False
    assert should_publish(_at(10, 0), schedule, DayState()) is False


def test_does_not_publish_when_daily_limit_reached():
    schedule = _schedule()
    day = DayState(date="2026-10-18", posts=3)
    assert should_publish(_at(14, 0), schedule, day) is False


def test_publishes_at_point_window():
    schedule = _schedule()
    assert should_publish(_at(22, 50), schedule, DayState()) is True
    assert should_publish(_at(22, 51), schedule, DayState()) is False


def test_min_gap_blocks_too_soon():
    schedule = _schedule(min_gap_minutes=45)
    day = DayState(date="2026-10-18", posts=1, last_post_at=_at(14, 0).isoformat())
    # Эффективный интервал растянут под окно, поэтому проверяем через него.
    from kino.schedule import effective_gap_minutes
    required = effective_gap_minutes(schedule)
    assert should_publish(_at(14, 0) + timedelta(minutes=required - 1), schedule, day) is False
    assert should_publish(_at(14, 0) + timedelta(minutes=required), schedule, day) is True


def test_min_gap_ignored_when_no_posts():
    schedule = _schedule()
    assert should_publish(_at(14, 0), schedule, DayState()) is True


def test_broken_last_post_at_does_not_block():
    """Испорченное время в state не должно навсегда запрещать посты."""
    schedule = _schedule()
    day = DayState(date="2026-10-18", posts=1, last_post_at="не дата")
    assert should_publish(_at(14, 0), schedule, day) is True


def test_new_day_resets_counter():
    """Вчерашний лимит не мешает сегодня."""
    schedule = _schedule()
    day = DayState(date="2026-10-17", posts=3, last_post_at=_at(16, 0, day=17).isoformat())
    assert should_publish(_at(14, 0, day=18), schedule, day) is True


def test_new_day_resets_gap():
    """Пауза считается внутри дня: вчерашний пост не блокирует сегодняшний."""
    schedule = _schedule(min_gap_minutes=45)
    day = DayState(date="2026-10-17", posts=1, last_post_at=_at(23, 55, day=17).isoformat())
    assert should_publish(_at(11, 5, day=18), schedule, day) is True


# --- next_slot ---------------------------------------------------------------


def test_next_slot_inside_window_is_now():
    schedule = _schedule()
    slot = next_slot(_at(14, 0), schedule, DayState())
    assert slot is not None
    assert slot <= _at(14, 0) + timedelta(minutes=1)


def test_next_slot_before_window_is_window_start():
    schedule = _schedule()
    slot = next_slot(_at(9, 0), schedule, DayState())
    assert slot.hour == 11 and slot.minute == 0


def test_next_slot_after_interval_goes_to_point():
    schedule = _schedule()
    slot = next_slot(_at(19, 0), schedule, DayState())
    assert slot.hour == 22 and slot.minute == 50


def test_next_slot_after_limit_is_tomorrow():
    schedule = _schedule()
    day = DayState(date="2026-10-18", posts=3)
    slot = next_slot(_at(14, 0), schedule, day)
    assert slot.day == 19
    assert slot.hour == 11


def test_next_slot_after_point_window_is_tomorrow_morning():
    schedule = _schedule()
    slot = next_slot(_at(23, 0), schedule, DayState())
    assert slot.day == 19
    assert slot.hour == 11


def test_next_slot_respects_gap():
    """Пауза сдвигает слот, но он остаётся внутри окна."""
    schedule = _schedule(min_gap_minutes=120)
    day = DayState(date="2026-10-18", posts=1, last_post_at=_at(14, 0).isoformat())
    slot = next_slot(_at(14, 30), schedule, day)
    assert slot is not None
    assert slot >= _at(15, 0)
    assert open_window(slot, schedule) is not None


# --- эффективный интервал (распределение по окну) ----------------------------


def test_effective_gap_stretches_to_window():
    """3 поста, окно 11:00-18:00 (420 мин), точка 22:50 → 2 поста на 420 мин."""
    from kino.schedule import effective_gap_minutes
    # interval_posts = 3 - 1 точка = 2; 420/2 = 210
    assert effective_gap_minutes(_schedule()) == 210


def test_effective_gap_keeps_manual_floor():
    """Ручной минимум остаётся полом: автоматика только растягивает."""
    from kino.schedule import effective_gap_minutes
    # min_gap 300 > вычисленных 210 — берём 300
    assert effective_gap_minutes(_schedule(min_gap_minutes=300)) == 300


def test_effective_gap_without_points():
    """Без точек все посты делят окно."""
    from kino.schedule import effective_gap_minutes
    schedule = ScheduleConfig(
        windows=parse_windows(["11:00-18:00"]),
        max_posts_per_day=3,
        min_gap_minutes=10,
    )
    assert effective_gap_minutes(schedule) == 140   # 420/3


def test_effective_gap_without_interval_windows():
    """Только точки — интервал остаётся ручным."""
    from kino.schedule import effective_gap_minutes
    schedule = ScheduleConfig(
        windows=parse_windows(["11:00", "22:50"]),
        max_posts_per_day=2,
        min_gap_minutes=45,
    )
    assert effective_gap_minutes(schedule) == 45


def test_posts_spread_across_window_not_clustered():
    """Главный кейс: посты идут по окну, а не выстреливают к 12:30."""
    schedule = _schedule()
    day = DayState()
    published: list[str] = []
    moment = _at(11, 0)
    for _ in range(24 * 60):
        if should_publish(moment, schedule, day):
            published.append(moment.strftime("%H:%M"))
            day = apply_post(day, moment, schedule)
        moment += timedelta(minutes=1)
    assert published == ["11:00", "14:30", "22:50"], published


def test_posts_spread_with_two_per_day():
    schedule = _schedule(max_posts_per_day=2)
    day = DayState()
    published: list[str] = []
    moment = _at(11, 0)
    for _ in range(24 * 60):
        if should_publish(moment, schedule, day):
            published.append(moment.strftime("%H:%M"))
            day = apply_post(day, moment, schedule)
        moment += timedelta(minutes=1)
    assert len(published) == 2
    assert published[0] == "11:00"
    # Второй пост — либо в дневном окне, либо в точке 22:50.
    assert published[1] == "22:50" or published[1] >= "14:00"


# --- apply_post --------------------------------------------------------------


def test_apply_post_increments_and_stamps():
    schedule = _schedule()
    day = apply_post(DayState(), _at(14, 0), schedule)
    assert day.posts == 1
    assert day.date == "2026-10-18"
    assert day.last_post_at.startswith("2026-10-18T14:00")


def test_apply_post_three_times_hits_limit():
    schedule = _schedule()
    day = DayState()
    for hour, minute in ((11, 0), (12, 0), (13, 0)):
        day = apply_post(day, _at(hour, minute), schedule)
    assert day.posts == 3
    assert should_publish(_at(14, 0), schedule, day) is False


def test_apply_post_on_new_day_starts_from_one():
    schedule = _schedule()
    day = DayState(date="2026-10-17", posts=3, last_post_at=_at(16, 0, day=17).isoformat())
    day = apply_post(day, _at(11, 30, day=18), schedule)
    assert day.posts == 1
    assert day.date == "2026-10-18"


# --- describe_day ------------------------------------------------------------


def test_describe_day_empty():
    assert "не было" in describe_day(DayState(), _schedule())


def test_describe_day_with_posts():
    schedule = _schedule()
    day = DayState(date="2026-10-18", posts=2, last_post_at=_at(14, 30).isoformat())
    text = describe_day(day, schedule)
    assert "2/3" in text
    assert "14:30" in text


def test_describe_day_tolerates_broken_time():
    schedule = _schedule()
    day = DayState(date="2026-10-18", posts=1, last_post_at="мусор")
    assert "1/3" in describe_day(day, schedule)


# --- реальный сценарий дня ---------------------------------------------------


def test_full_day_scenario():
    """Полный день: посты распределены по окну, вечерний в 22:50, ночью тишина."""
    schedule = _schedule()   # max 3, окна 11:00-18:00 и 22:50 → интервал 210 мин
    day = DayState()

    # 10:00 — окна нет
    assert should_publish(_at(10, 0), schedule, day) is False

    # 11:00 — публикуем
    assert should_publish(_at(11, 0), schedule, day) is True
    day = apply_post(day, _at(11, 0), schedule)

    # 12:00 — интервал 210 мин ещё не прошёл (не кластеризуем)
    assert should_publish(_at(12, 0), schedule, day) is False

    # 14:30 — ровно 210 мин, можно
    assert should_publish(_at(14, 30), schedule, day) is True
    day = apply_post(day, _at(14, 30), schedule)

    # 18:30 — окно закрылось
    assert should_publish(_at(18, 30), schedule, day) is False

    # 22:50 — точка, лимит ещё не исчерпан (2 из 3)
    assert should_publish(_at(22, 50), schedule, day) is True
    day = apply_post(day, _at(22, 50), schedule)

    # 22:51 — точка прошла, окна нет
    assert should_publish(_at(22, 51), schedule, day) is False

    # 23:30 и ночью — тишина
    assert should_publish(_at(23, 30), schedule, day) is False
    assert should_publish(_at(3, 0), schedule, day) is False

    assert day.posts == 3

    # Следующий день начинается заново
    assert should_publish(_at(11, 0, day=19), schedule, day) is True