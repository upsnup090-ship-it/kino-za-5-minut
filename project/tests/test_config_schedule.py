"""Регрессии секции schedule в конфиге: чтение и валидация.

Опечатка в окнах публикации должна падать на старте (ConfigError), а не
превращаться в «бот молчит сутки».
"""

from __future__ import annotations

import pytest
import yaml
from pathlib import Path

from kino.config import ConfigError, load_config

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "config" / "settings.yaml"


def _base_env(tmp_path) -> Path:
    env = tmp_path / "env.test"
    env.write_text(
        "BOT_TOKEN=123:test\nCHANNEL_CHAT_ID=-100\nADMIN_CHAT_ID=1\n"
        "LLM_PROVIDER=apimart\nLLM_BASE_URL=https://api.apimart.ai/v1\n"
        "LLM_API_KEY=sk-test\n",
        encoding="utf-8",
    )
    return env


def _settings_with(**schedule_overrides) -> dict:
    """Настройки по умолчанию, но с переопределённой секцией schedule."""
    data = yaml.safe_load(SETTINGS.read_text(encoding="utf-8"))
    base = {
        "windows": ["11:00-18:00", "22:50"],
        "max_posts_per_day": 3,
        "min_gap_minutes": 45,
        "timezone": "Europe/Moscow",
        "tick_seconds": 60,
    }
    base.update(schedule_overrides)
    data["schedule"] = base
    return data


def _write_settings(tmp_path, data: dict) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


class _FakeYAML:
    """Подменяет _load_yaml: для settings.yaml отдаёт файл из tmp_path."""

    def __init__(self, tmp_path, **schedule_overrides):
        self.fake = _write_settings(tmp_path, _settings_with(**schedule_overrides))
        self._orig = None

    def __enter__(self):
        import kino.config as cfgmod

        self._orig = cfgmod._load_yaml

        def fake_load_yaml(path: Path):
            if path.name == "settings.yaml":
                return yaml.safe_load(self.fake.read_text(encoding="utf-8"))
            return self._orig(path)

        cfgmod._load_yaml = fake_load_yaml
        return self

    def __exit__(self, *exc):
        import kino.config as cfgmod

        cfgmod._load_yaml = self._orig


def test_schedule_loaded_from_settings(tmp_path):
    cfg = load_config(str(_base_env(tmp_path)))
    assert cfg.settings.schedule_windows == ["11:00-18:00", "22:50"]
    assert cfg.settings.max_posts_per_day == 3
    assert cfg.settings.timezone == "Europe/Moscow"
    assert cfg.settings.tick_seconds == 60
    assert cfg.settings.min_gap_minutes == 45


def test_bad_window_raises_config_error(tmp_path):
    """Мусор в windows → ConfigError на старте."""
    with _FakeYAML(tmp_path, windows=["11:00-18:00", "не-время"]):
        with pytest.raises(ConfigError, match="расписание"):
            load_config(str(_base_env(tmp_path)))


def test_bad_timezone_raises_config_error(tmp_path):
    with _FakeYAML(tmp_path, timezone="Несуществующая/Зона"):
        with pytest.raises(ConfigError, match="расписание|зона"):
            load_config(str(_base_env(tmp_path)))


def test_zero_posts_per_day_raises(tmp_path):
    with _FakeYAML(tmp_path, max_posts_per_day=0):
        with pytest.raises(ConfigError, match="расписание"):
            load_config(str(_base_env(tmp_path)))


def test_negative_gap_raises(tmp_path):
    with _FakeYAML(tmp_path, min_gap_minutes=-5):
        with pytest.raises(ConfigError, match="расписание"):
            load_config(str(_base_env(tmp_path)))


def test_no_schedule_section_is_backward_compatible(tmp_path):
    """Старый settings.yaml без секции schedule — не падаем (пустое расписание)."""
    import kino.config as cfgmod

    data = yaml.safe_load(SETTINGS.read_text(encoding="utf-8"))
    data.pop("schedule", None)
    fake = _write_settings(tmp_path, data)
    original = cfgmod._load_yaml

    def fake_load_yaml(path: Path):
        if path.name == "settings.yaml":
            return yaml.safe_load(fake.read_text(encoding="utf-8"))
        return original(path)

    cfgmod._load_yaml = fake_load_yaml
    try:
        cfg = load_config(str(_base_env(tmp_path)))
        assert cfg.settings.schedule_windows == []
    finally:
        cfgmod._load_yaml = original