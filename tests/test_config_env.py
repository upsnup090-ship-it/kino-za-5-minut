"""Проверка чтения LLM-переменных окружения в config.load_config.

Тесты изолированы: чистят окружение и подсовывают свой env_file, чтобы не
зависеть от реального .env.test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kino.config import Config, ConfigError, Settings, load_config

ROOT = Path(__file__).resolve().parents[1]

_ENV_KEYS = (
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "DRAFT_MODEL",
    "JUDGE_MODEL",
    "JUDGE_FALLBACK_MODEL",
    "LLM_PROXY_URL",
    "PROXY_URL",
    "BOT_TOKEN",
    "APIMART_API_KEY",
    "GEMINI_API_KEY",
)


@pytest.fixture
def clean_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _write_env(tmp_path: Path, body: str) -> str:
    path = tmp_path / "env.test"
    path.write_text(body, encoding="utf-8")
    return str(path)


def _base_env(extra: str = "") -> str:
    return (
        "BOT_TOKEN=123:test\n"
        "CHANNEL_CHAT_ID=-100\n"
        "ADMIN_CHAT_ID=1\n"
        "LLM_PROVIDER=apimart\n"
        "LLM_BASE_URL=https://api.apimart.ai/v1\n"
        "LLM_API_KEY=sk-apimart-test\n"
        + extra
    )


def test_apimart_base_url_and_key_are_read(clean_env, tmp_path):
    env_file = _write_env(tmp_path, _base_env("DRAFT_MODEL=gpt-4o-mini\nJUDGE_MODEL=gpt-4o-mini\n"))
    cfg = load_config(env_file)
    assert cfg.llm_base_url == "https://api.apimart.ai/v1"
    assert cfg.llm_api_key == "sk-apimart-test"
    assert cfg.draft_model == "gpt-4o-mini"
    assert cfg.judge_model == "gpt-4o-mini"


def test_env_overrides_yaml_provider(clean_env, tmp_path):
    """LLM_BASE_URL из env побеждает providers.<name>.base_url из settings.yaml."""
    env_file = _write_env(
        tmp_path,
        _base_env("LLM_BASE_URL=https://api.apimart.ai/v1/custom\n"),
    )
    cfg = load_config(env_file)
    assert cfg.llm_base_url == "https://api.apimart.ai/v1/custom"


def test_provider_without_env_uses_yaml(clean_env, tmp_path):
    """LLM_PROVIDER=apimart без LLM_BASE_URL берёт base_url из settings.yaml."""
    env_file = _write_env(
        tmp_path,
        "BOT_TOKEN=123:test\nLLM_PROVIDER=apimart\nAPIMART_API_KEY=sk-from-yaml\n",
    )
    cfg = load_config(env_file)
    assert cfg.llm_base_url == "https://api.apimart.ai/v1"
    assert cfg.llm_api_key == "sk-from-yaml"


def test_missing_base_url_raises(clean_env, tmp_path):
    env_file = _write_env(tmp_path, "BOT_TOKEN=123:test\nLLM_PROVIDER=unknown\n")
    with pytest.raises(ConfigError, match="LLM_BASE_URL"):
        load_config(env_file)


def test_placeholder_api_key_rejected(clean_env, tmp_path):
    env_file = _write_env(
        tmp_path,
        "BOT_TOKEN=123:test\nLLM_BASE_URL=https://api.apimart.ai/v1\n"
        "LLM_API_KEY=replace_me\n",
    )
    with pytest.raises(ConfigError, match="LLM_API_KEY"):
        load_config(env_file)


def test_llm_proxy_falls_back_to_proxy_url(clean_env, tmp_path):
    """LLM_PROXY_URL не задан — LLM идёт через PROXY_URL."""
    env_file = _write_env(tmp_path, _base_env("PROXY_URL=http://tg:pass@host:1\n"))
    cfg = load_config(env_file)
    assert cfg.llm_proxy_url == "http://tg:pass@host:1"
    assert cfg.proxy_url == "http://tg:pass@host:1"


def test_empty_llm_proxy_disables_fallback(clean_env, tmp_path):
    """LLM_PROXY_URL= (пустой) — LLM напрямую, Telegram-прокси не подставляется."""
    env_file = _write_env(
        tmp_path,
        _base_env("PROXY_URL=http://tg:pass@host:1\nLLM_PROXY_URL=\n"),
    )
    cfg = load_config(env_file)
    assert cfg.llm_proxy_url is None
    assert cfg.proxy_url == "http://tg:pass@host:1"


def test_explicit_llm_proxy_wins(clean_env, tmp_path):
    env_file = _write_env(
        tmp_path,
        _base_env("PROXY_URL=http://tg:pass@host:1\nLLM_PROXY_URL=http://llm:pass@host:2\n"),
    )
    cfg = load_config(env_file)
    assert cfg.llm_proxy_url == "http://llm:pass@host:2"


def test_stats_path_env_override(clean_env, tmp_path):
    """STATS_PATH из env попадает в конфиг абсолютным путём (п.38)."""
    env_file = _write_env(tmp_path, _base_env("STATS_PATH=logs/other_stats.csv\n"))
    cfg = load_config(env_file)
    assert cfg.stats_path == ROOT / "logs" / "other_stats.csv"


def test_stats_path_absolute_env_wins(clean_env, tmp_path):
    target = tmp_path / "format_stats.csv"
    env_file = _write_env(tmp_path, _base_env(f"STATS_PATH={target}\n"))
    cfg = load_config(env_file)
    assert cfg.stats_path == target


def test_default_config_has_no_stats_path():
    """Прямой Config() (тесты, скрипты) не пишет CSV — п.38 журнала.

    Прогон тестов не должен дописывать строки source=test в боевой
    logs/format_stats.csv; бой получает путь явно из load_config.
    """
    assert Config(settings=Settings()).stats_path is None
