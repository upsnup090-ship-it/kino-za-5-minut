"""Тесты состояния."""

from __future__ import annotations

import json

from kino.state import State, load_state, record_choice, save_state, set_state_day, state_day


def test_load_state_missing_file(tmp_path):
    state = load_state(tmp_path / "nope.json")
    assert state.used_links == []
    assert state.used_sigs == []
    # Новые поля дня — пустые (сегодня постов не было).
    assert state.day == ""
    assert state.posts_today == 0
    assert state.last_post_at == ""


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = State(used_links=["a", "b"], used_sigs=[["dune", "two"]])
    save_state(path, state, max_links=120, max_sigs=60)
    loaded = load_state(path)
    assert loaded.used_links == ["a", "b"]
    assert loaded.used_sigs == [["dune", "two"]]


def test_save_state_trims_to_limits(tmp_path):
    path = tmp_path / "state.json"
    state = State(used_links=[str(i) for i in range(200)])
    save_state(path, state, max_links=120, max_sigs=60)
    loaded = load_state(path)
    assert len(loaded.used_links) == 120
    assert loaded.used_links[-1] == "199"


def test_load_state_corrupt_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    state = load_state(path)
    assert state.used_links == []


def test_record_choice_appends_and_trims():
    state = State()
    record_choice(state, "link1", ["dune"], max_links=2, max_sigs=2)
    record_choice(state, "link2", ["two"], max_links=2, max_sigs=2)
    record_choice(state, "link3", ["three"], max_links=2, max_sigs=2)
    assert state.used_links == ["link2", "link3"]
    assert len(state.used_sigs) == 2


def test_save_state_is_atomic_no_tmp_left(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, State(used_links=["x"]), max_links=120, max_sigs=60)
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".state-")]
    assert leftovers == []
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["used_links"] == ["x"]


# --- поля дня (24/7) ---------------------------------------------------------


def test_day_fields_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = State(day="2026-10-18", posts_today=2, last_post_at="2026-10-18T14:30:00+03:00")
    save_state(path, state, max_links=120, max_sigs=60)
    loaded = load_state(path)
    assert loaded.day == "2026-10-18"
    assert loaded.posts_today == 2
    assert loaded.last_post_at == "2026-10-18T14:30:00+03:00"


def test_load_state_old_file_without_day_fields(tmp_path):
    """Обратная совместимость: старый state.json без новых ключей."""
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"used_links": ["a"], "used_sigs": [["x"]]}),
        encoding="utf-8",
    )
    state = load_state(path)
    assert state.used_links == ["a"]
    assert state.day == ""
    assert state.posts_today == 0


def test_load_state_ignores_bad_day_fields(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"day": 123, "posts_today": -5, "last_post_at": ["x"]}),
        encoding="utf-8",
    )
    state = load_state(path)
    assert state.day == ""
    assert state.posts_today == 0
    assert state.last_post_at == ""


def test_save_state_writes_day_fields(tmp_path):
    path = tmp_path / "state.json"
    save_state(
        path,
        State(day="2026-10-18", posts_today=3, last_post_at="2026-10-18T22:50:00+03:00"),
        max_links=120,
        max_sigs=60,
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["day"] == "2026-10-18"
    assert data["posts_today"] == 3
    assert data["last_post_at"] == "2026-10-18T22:50:00+03:00"


# --- мост State <-> DayState (для serve) ------------------------------------


def test_state_day_bridge():
    state = State(day="2026-10-18", posts_today=2, last_post_at="2026-10-18T14:00:00+03:00")
    day = state_day(state)
    assert day.date == "2026-10-18"
    assert day.posts == 2
    assert day.last_post_at == "2026-10-18T14:00:00+03:00"


def test_state_day_bridge_empty():
    day = state_day(State())
    assert day.date == ""
    assert day.posts == 0
    assert day.last_post_at == ""


def test_set_state_day_roundtrip():
    from kino.schedule import DayState

    state = State()
    set_state_day(state, DayState(date="2026-10-18", posts=3, last_post_at="2026-10-18T22:50:00+03:00"))
    assert state.day == "2026-10-18"
    assert state.posts_today == 3
    assert state.last_post_at == "2026-10-18T22:50:00+03:00"


def test_set_state_day_overwrites():
    from kino.schedule import DayState

    state = State(day="2026-10-17", posts_today=3)
    set_state_day(state, DayState(date="2026-10-18", posts=1))
    assert state.day == "2026-10-18"
    assert state.posts_today == 1