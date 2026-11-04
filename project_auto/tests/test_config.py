"""Проверка конфигурационных файлов."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"


def _load(name: str) -> dict:
    path = CONFIG_DIR / name
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_sources_are_strings():
    cfg = _load("sources.yaml")
    sources = cfg["sources"]
    assert isinstance(sources, list)
    assert len(sources) > 0
    for item in sources:
        assert isinstance(item, dict)
        assert isinstance(item["name"], str)
        assert isinstance(item["url"], str)


def test_formats_lists_are_strings():
    cfg = _load("formats.yaml")
    for key in ("bad", "sad"):
        items = cfg[key]
        assert isinstance(items, list)
        for item in items:
            assert isinstance(item, str), f"{key} содержит не строку: {item!r}"


def test_settings_types():
    cfg = _load("settings.yaml")
    assert isinstance(cfg["collect"]["timeout_seconds"], int)
    assert isinstance(cfg["collect"]["snippet_length"], int)
    assert isinstance(cfg["collect"]["freshness_days"], int)
    assert isinstance(cfg["collect"]["top_n"], int)
    assert isinstance(cfg["collect"]["allow_stale_fallback"], bool)
    assert isinstance(cfg["collect"]["user_agent"], str)
    assert isinstance(cfg["collect"]["max_workers"], int)
    assert isinstance(cfg["llm"]["draft"]["temperature"], float)
    assert isinstance(cfg["llm"]["draft"]["max_tokens"], int)
    assert isinstance(cfg["llm"]["draft"]["top_p"], float)
    assert isinstance(cfg["llm"]["judge"]["temperature"], float)
    assert isinstance(cfg["llm"]["judge"]["max_tokens"], int)
    assert isinstance(cfg["llm"]["timeout_seconds"], int)
    assert isinstance(cfg["wiki"]["timeout_seconds"], int)
    assert isinstance(cfg["wiki"]["user_agent"], str)
    assert isinstance(cfg["retry"]["max_attempts"], int)
    assert isinstance(cfg["retry"]["backoff_seconds"], int)
    assert isinstance(cfg["caption"]["max_length"], int)
    assert isinstance(cfg["caption"]["hard_limit"], int)
    assert isinstance(cfg["publish"]["parse_mode"], str)


# --- Фаза 2: kind в sources.yaml + routing в formats.yaml ---------------------

def test_sources_have_kind():
    cfg = _load("sources.yaml")
    for item in cfg["sources"]:
        assert "kind" in item, f"{item['name']}: нет kind"
        assert item["kind"] in ("mixed", "video", "trailer", "boxoffice", "archive")


def test_yt_sources_are_video():
    cfg = _load("sources.yaml")
    for item in cfg["sources"]:
        if item["name"].startswith("YT/"):
            assert item["kind"] == "video", item["name"]


def test_press_sources_are_mixed():
    cfg = _load("sources.yaml")
    for item in cfg["sources"]:
        if not item["name"].startswith("YT/"):
            assert item["kind"] == "mixed", item["name"]


def test_formats_routing_exists():
    cfg = _load("formats.yaml")
    assert "routing" in cfg, "formats.yaml: нет секции routing"
    routing = cfg["routing"]
    assert isinstance(routing, dict)
    assert "enabled" in routing
    assert "fallback" in routing
    assert routing["enabled"] is True
    assert routing["fallback"] == "news"


def test_formats_routing_covers_all_weights():
    cfg = _load("formats.yaml")
    weights = cfg["weights"]
    routing = cfg["routing"]
    missing = [fmt for fmt in weights if fmt not in routing and fmt != "fallback"]
    assert not missing, f"нет правил для {missing}"


def test_formats_routing_kinds_are_known():
    from kino.routing import KINDS

    cfg = _load("formats.yaml")
    routing = cfg["routing"]
    for fmt, rule in routing.items():
        if fmt in ("enabled", "fallback"):
            continue
        kinds = rule.get("kinds") or []
        unknown = set(kinds) - set(KINDS)
        assert not unknown, f"{fmt}: неизвестные классы {unknown}"


def test_formats_routing_signals_are_known():
    from kino.routing import SIGNALS

    cfg = _load("formats.yaml")
    routing = cfg["routing"]
    for fmt, rule in routing.items():
        if fmt in ("enabled", "fallback"):
            continue
        require = rule.get("require") or []
        unknown = set(require) - set(SIGNALS)
        assert not unknown, f"{fmt}: неизвестные требования {unknown}"


def test_formats_weights_sum_113():
    cfg = _load("formats.yaml")
    total = sum(cfg["weights"].values())
    assert total == 113, f"сумма весов {total}, ожидалось 113"


def test_weights_s2_calibration():
    """Калибровка S2 (§9.2), применена 31.10: news 55%→~24%. Канон весов."""
    cfg = _load("formats.yaml")
    w = cfg["weights"]
    expected = {"news": 8, "facts": 12, "poll": 10, "quote": 10, "collage": 10, "when": 16}
    for fmt, value in expected.items():
        assert w[fmt] == value, f"{fmt}={w[fmt]}, ожидалось {value}"