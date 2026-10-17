"""Тесты состояния."""

import json

from kino.state import State, load_state, record_choice, save_state


def test_load_state_missing_file(tmp_path):
    state = load_state(tmp_path / "nope.json")
    assert state.used_links == []
    assert state.used_sigs == []


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