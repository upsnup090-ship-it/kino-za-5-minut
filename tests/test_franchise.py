"""Тесты франшизного кулдауна (шаг 9): словарь, детектор, окно, отбор."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from kino.config import Config, Settings
from kino.choose import choose
from kino.collect import FeedItem
from kino.franchise import (
    DEFAULT_COOLDOWN_HOURS,
    Franchise,
    FranchiseError,
    config_path,
    describe_blocked,
    detect,
    filter_cooled_down,
    load_franchises,
    recent_keys,
)

ROOT = Path(__file__).resolve().parents[1]


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "franchises.yaml"
    path.write_text(body, encoding="utf-8")
    return path


_DICT = """
franchises:
  - key: dune
    title: Дюна
    phrases: ["дюн*", "dune"]
  - key: marvel
    title: Marvel
    phrases: ["marvel", "марвел"]
"""


# --- словарь -----------------------------------------------------------------


def test_load_franchises_parses(tmp_path):
    franchises = load_franchises(_write(tmp_path, _DICT))
    assert [f.key for f in franchises] == ["dune", "marvel"]
    assert franchises[0].title == "Дюна"
    assert "дюн*" in franchises[0].phrases


def test_missing_file_means_disabled(tmp_path):
    assert load_franchises(tmp_path / "nope.yaml") == []
    assert load_franchises(None) == []


def test_empty_franchises_list(tmp_path):
    assert load_franchises(_write(tmp_path, "franchises: []\n")) == []


def test_load_rejects_non_list(tmp_path):
    with pytest.raises(FranchiseError, match="списком"):
        load_franchises(_write(tmp_path, "franchises: dune\n"))


def test_load_rejects_missing_key(tmp_path):
    with pytest.raises(FranchiseError, match="нет key"):
        load_franchises(_write(tmp_path, "franchises:\n  - title: Дюна\n"))


def test_load_rejects_empty_phrases(tmp_path):
    with pytest.raises(FranchiseError, match="phrases"):
        load_franchises(_write(tmp_path, "franchises:\n  - key: dune\n    phrases: []\n"))


def test_load_rejects_broken_yaml(tmp_path):
    with pytest.raises(FranchiseError, match="YAML"):
        load_franchises(_write(tmp_path, "franchises: [\n"))


def test_shipped_dictionary_loads():
    """Боевой config/franchises.yaml должен читаться и быть непустым."""
    franchises = load_franchises(ROOT / "config" / "franchises.yaml")
    assert len(franchises) >= 10
    keys = [f.key for f in franchises]
    assert len(keys) == len(set(keys)), "ключи франшиз должны быть уникальны"


# --- детектор ----------------------------------------------------------------


def _dict() -> list[Franchise]:
    return [
        Franchise(key="dune", title="Дюна", phrases=("дюн*", "dune")),
        Franchise(key="marvel", title="Marvel", phrases=("marvel", "марвел")),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Dune 3 gets release date",
        "Вильнёв рассказал о Дюне 3",
        "ДЮНА: третья часть",
        "Съёмки «Дюны» завершены",
    ],
)
def test_detect_finds_dune(text):
    assert [f.key for f in detect(text, _dict())] == ["dune"]


@pytest.mark.parametrize(
    "text",
    ["Дюжина новых актёров", "Duneland festival", "Аватар 3", ""],
)
def test_detect_does_not_match_substrings(text):
    assert detect(text, _dict()) == []


def test_detect_multiple():
    keys = [f.key for f in detect("Marvel и Дюна: кроссовер", _dict())]
    assert keys == ["dune", "marvel"]


def test_detect_without_dictionary():
    assert detect("Dune 3", []) == []


def test_describe_blocked():
    franchises = _dict()
    assert describe_blocked(franchises[:1], {"dune"}) == "Дюна"
    assert describe_blocked(franchises, {"dc"}) == ""


# --- окно кулдауна -----------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 10, 31, 21, 20, tzinfo=datetime.now().astimezone().tzinfo)


def test_recent_keys_inside_window():
    now = _now()
    published = [("dune", (now - timedelta(hours=10)).isoformat())]
    assert recent_keys(published, now, 36) == {"dune"}


def test_recent_keys_outside_window():
    now = _now()
    published = [("dune", (now - timedelta(hours=40)).isoformat())]
    assert recent_keys(published, now, 36) == set()


def test_recent_keys_boundary():
    now = _now()
    published = [("dune", (now - timedelta(hours=36)).isoformat())]
    assert recent_keys(published, now, 36) == set()


def test_recent_keys_ignores_broken_timestamp():
    now = _now()
    assert recent_keys([("dune", "не дата")], now, 36) == set()


def test_recent_keys_ignores_empty_key():
    now = _now()
    assert recent_keys([("", now.isoformat())], now, 36) == set()


def test_recent_keys_rejects_bad_cooldown():
    with pytest.raises(FranchiseError):
        recent_keys([], _now(), 0)


def test_default_cooldown_is_36():
    assert DEFAULT_COOLDOWN_HOURS == 36


def test_filter_cooled_down():
    franchises = _dict()
    texts = [("Dune 3", franchises[:1]), ("Avatar", [])]
    assert filter_cooled_down(texts, {"dune"}) == [False, True]


def test_filter_blocks_when_any_franchise_cooled():
    franchises = _dict()
    texts = [("Marvel и Дюна", franchises)]
    assert filter_cooled_down(texts, {"dune"}) == [False]


# --- конфиг ------------------------------------------------------------------


def test_config_path_relative(tmp_path):
    assert config_path("config/franchises.yaml", tmp_path) == tmp_path / "config" / "franchises.yaml"


def test_config_path_empty_means_disabled(tmp_path):
    assert config_path("", tmp_path) is None
    assert config_path(None, tmp_path) is None


# --- интеграция с choose -----------------------------------------------------


def _cfg(tmp_path: Path, **kwargs) -> Config:
    settings = dict(
        window_size=10,
        franchise_cooldown_hours=36,
        franchises_path=str(ROOT / "config" / "franchises.yaml"),
    )
    settings.update(kwargs)
    return Config(
        sources=[],
        weights={"news": 1},
        bad=[],
        sad=[],
        settings=Settings(**settings),
        state_path=tmp_path / "state.json",
    )


def _item(title: str, link: str = "") -> FeedItem:
    return FeedItem(
        source="t", title=title, link=link or f"https://x/{title[:10]}",
        pub_date="", ts=0.0, snippet=title,
    )


def test_choose_skips_franchise_in_cooldown(tmp_path):
    cfg = _cfg(tmp_path)
    now = _now()
    history = [("dune", (now - timedelta(hours=5)).isoformat())]
    items = [_item("Dune 3 gets release date", "https://x/dune3"),
             _item("Avatar 3 trailer drops", "https://x/avatar3")]

    for _ in range(10):
        choice = choose(items, cfg, set(), [], franchise_history=history, now=now)
        assert "Avatar" in choice.item.title


def test_choose_allows_after_cooldown(tmp_path):
    cfg = _cfg(tmp_path)
    now = _now()
    history = [("dune", (now - timedelta(hours=40)).isoformat())]
    items = [_item("Dune 3 gets release date", "https://x/dune3")]

    choice = choose(items, cfg, set(), [], franchise_history=history, now=now)
    assert "Dune" in choice.item.title


def test_choose_without_dictionary_does_not_filter(tmp_path):
    cfg = _cfg(tmp_path, franchises_path=str(tmp_path / "none.yaml"))
    now = _now()
    history = [("dune", now.isoformat())]
    items = [_item("Dune 3 gets release date", "https://x/dune3")]

    choice = choose(items, cfg, set(), [], franchise_history=history, now=now)
    assert "Dune" in choice.item.title


def test_choose_zero_cooldown_disables_rule(tmp_path):
    cfg = _cfg(tmp_path, franchise_cooldown_hours=0)
    now = _now()
    history = [("dune", now.isoformat())]
    items = [_item("Dune 3 gets release date", "https://x/dune3")]

    choice = choose(items, cfg, set(), [], franchise_history=history, now=now)
    assert "Dune" in choice.item.title


def test_choose_keeps_items_when_everything_cooled_down(tmp_path):
    """Кулдаун не должен оставить прогон без темы: лучше пост, чем тишина."""
    cfg = _cfg(tmp_path)
    now = _now()
    history = [("dune", now.isoformat())]
    items = [_item("Dune 3 gets release date", "https://x/dune3")]

    choice = choose(items, cfg, set(), [], franchise_history=history, now=now)
    assert "Dune" in choice.item.title


def test_choose_broken_dictionary_does_not_crash(tmp_path):
    broken = tmp_path / "broken.yaml"
    broken.write_text("franchises: [\n", encoding="utf-8")
    cfg = _cfg(tmp_path, franchises_path=str(broken))
    now = _now()
    history = [("dune", now.isoformat())]
    items = [_item("Dune 3", "https://x/dune3")]

    choice = choose(items, cfg, set(), [], franchise_history=history, now=now)
    assert "Dune" in choice.item.title
