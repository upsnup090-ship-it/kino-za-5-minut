"""Валидация routing в config.py (Фаза 2, docs/source-routing-design.md §5.3).

Проверяет:
- unknown kind у источника → ConfigError на старте;
- unknown kind / signal в правиле формата → ConfigError на старте;
- формат из weights без правила → ConfigError (иначе это тихий мёртвый формат);
- секции routing нет вовсе → берётся DEFAULT_ROUTING из kino.routing.

`_load_yaml` подменяем ПОФАЙЛОВО: глобальная подмена одним словарём роняла
загрузку на пустом sources.yaml раньше, чем доходила до проверяемой ошибки,
и тесты проходили по неверной причине.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from kino import routing
from kino.config import ConfigError, load_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"

#: Минимальное окружение, без которого load_config требует BOT_TOKEN.
ENV_MOCK = {
    "LLM_BASE_URL": "http://llm.test",
    "LLM_API_KEY": "k",
    "BOT_TOKEN": "test-token",
    "CHANNEL_CHAT_ID": "-100test",
    "ADMIN_CHAT_ID": "-100admin",
}


def _load(name: str) -> dict:
    """Читает config/<name> как есть (копия, правки не трогают файл)."""
    return yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))


def _patch_config(**overrides: dict):
    """Подменяет чтение указанных файлов конфига, остальные — с диска.

    Ключи — имена файлов (`formats.yaml`, `sources.yaml`), значения — словари.
    """

    def mock(path):
        name = Path(path).name
        if name in overrides:
            # Глубокая копия через YAML: правки в тесте не должны влиять на диск.
            return yaml.safe_load(yaml.dump(overrides[name], allow_unicode=True))
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))

    return patch("kino.config._load_yaml", side_effect=mock)


def _load_with(**overrides: dict):
    """load_config с подменёнными файлами и тестовым окружением."""
    with patch.dict("os.environ", ENV_MOCK, clear=False), _patch_config(**overrides):
        return load_config()


# --- базовая загрузка --------------------------------------------------------

def test_load_config_with_routing():
    cfg = _load_with()
    assert isinstance(cfg.routing, dict)
    assert cfg.routing["enabled"] is True
    assert cfg.routing["fallback"] == "news"
    # Маппинг §4: у `when` есть и классы, и требование даты.
    assert cfg.routing["when"]["require"] == ["date"]


def test_source_kinds_loaded():
    cfg = _load_with()
    by_name = {s.name: s.kind for s in cfg.sources}
    assert by_name["Deadline"] == "mixed"
    assert by_name["YT/IGN"] == "video"
    assert by_name["Reddit/boxoffice"] == "mixed"  # решение владельца 5


# --- ошибки на старте --------------------------------------------------------

def test_unknown_kind_in_sources_raises():
    sources = _load("sources.yaml")
    sources["sources"].append({"name": "Bad", "url": "http://test", "kind": "unknown"})
    with pytest.raises(ConfigError, match="неизвестный kind"):
        _load_with(**{"sources.yaml": sources})


def test_source_without_kind_defaults_to_mixed():
    sources = _load("sources.yaml")
    sources["sources"][0].pop("kind")
    cfg = _load_with(**{"sources.yaml": sources})
    assert cfg.sources[0].kind == "mixed"


def test_unknown_kind_in_routing_raises():
    formats = _load("formats.yaml")
    formats["routing"]["facts"]["kinds"] = ["archive", "unknown_kind"]
    with pytest.raises(ConfigError, match="unknown_kind"):
        _load_with(**{"formats.yaml": formats})


def test_unknown_signal_in_routing_raises():
    formats = _load("formats.yaml")
    formats["routing"]["when"]["require"] = ["date", "unknown_signal"]
    with pytest.raises(ConfigError, match="unknown_signal"):
        _load_with(**{"formats.yaml": formats})


def test_missing_rule_for_weight_raises():
    """Формат в weights без правила — тихий мёртвый формат. Должно падать."""
    formats = _load("formats.yaml")
    del formats["routing"]["poll"]
    with pytest.raises(ConfigError, match="poll"):
        _load_with(**{"formats.yaml": formats})


def test_routing_not_a_dict_raises():
    formats = _load("formats.yaml")
    formats["routing"] = ["не", "словарь"]
    with pytest.raises(ConfigError, match="routing должен быть словарём"):
        _load_with(**{"formats.yaml": formats})


def test_routing_enabled_must_be_bool():
    formats = _load("formats.yaml")
    formats["routing"]["enabled"] = "yes"
    with pytest.raises(ConfigError, match="enabled"):
        _load_with(**{"formats.yaml": formats})


# --- фолбэк на DEFAULT_ROUTING ------------------------------------------------

def test_routing_absent_falls_back_to_default():
    """Нет секции routing — канон из кода, загрузка не падает."""
    formats = _load("formats.yaml")
    del formats["routing"]
    cfg = _load_with(**{"formats.yaml": formats})
    assert cfg.routing["fallback"] == routing.DEFAULT_ROUTING["fallback"]
    assert cfg.routing["facts"] == routing.DEFAULT_ROUTING["facts"]


def test_default_routing_covers_every_weight():
    """Канон из кода не должен отстать от weights — иначе фолбэк нерабочий."""
    weights = _load("formats.yaml")["weights"]
    missing = [fmt for fmt in weights if fmt not in routing.DEFAULT_ROUTING]
    assert not missing, f"в DEFAULT_ROUTING нет правил для {missing}"


def test_config_routing_matches_default():
    """Секция в конфиге и канон в коде не разъехались (дрейф §4)."""
    cfg = _load_with()
    for fmt, rule in routing.DEFAULT_ROUTING.items():
        if fmt in ("enabled", "fallback"):
            continue
        assert fmt in cfg.routing, f"{fmt} пропал из formats.yaml: routing"
        assert cfg.routing[fmt]["kinds"] == rule["kinds"], fmt
        assert cfg.routing[fmt]["require"] == rule["require"], fmt