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