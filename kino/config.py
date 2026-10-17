"""Загрузка конфигурации и переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"


class ConfigError(Exception):
    """Ошибка конфигурации."""


@dataclass
class Source:
    name: str
    url: str


@dataclass
class Settings:
    collect_timeout: float = 15.0
    snippet_length: int = 300
    freshness_days: int = 48
    top_n: int = 25
    allow_stale_fallback: bool = True
    user_agent: str = "KinoZa5Minut/1.0"
    max_workers: int = 6
    window_size: int = 10
    state_max_links: int = 120
    state_max_signatures: int = 60
    draft_temperature: float = 0.75
    draft_max_tokens: int = 1500
    draft_top_p: float = 0.9
    judge_temperature: float = 0.1
    judge_max_tokens: int = 1300
    llm_timeout: float = 60.0
    wiki_timeout: float = 15.0
    wiki_user_agent: str = "KinoZa5Minut/1.0"
    telegram_timeout: float = 30.0
    retry_max_attempts: int = 5
    retry_backoff: float = 2.0
    caption_max_length: int = 950
    caption_hard_limit: int = 1000
    parse_mode: str = "HTML"


@dataclass
class Config:
    sources: list[Source] = field(default_factory=list)
    weights: dict[str, int] = field(default_factory=dict)
    bad: list[str] = field(default_factory=list)
    sad: list[str] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)
    llm_base_url: str = ""
    llm_api_key: str = ""
    draft_model: str = ""
    judge_model: str = ""
    judge_fallback_model: str = ""
    bot_token: str = ""
    channel_chat_id: str = ""
    admin_chat_id: str = ""
    proxy_url: str | None = None
    state_path: Path = ROOT / "data" / "state.json"
    stats_path: Path = ROOT / "logs" / "format_stats.csv"
    source_mode: str = "rss"
    horizon_dir: Path = ROOT / "horizon"
    horizon_summaries_dir: Path = ROOT / "horizon" / "data" / "summaries"
    horizon_run: bool = True
    horizon_hours: int = 24
    horizon_timeout: float = 600.0


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as exc:
        raise ConfigError(f"нет файла конфигурации: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"ошибка YAML в {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"конфиг {path} должен быть словарём")
    return data


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith("replace_me"):
        raise ConfigError(f"переменная окружения {name} не задана")
    return value


def _optional_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value.startswith("replace_me"):
        return ""
    return value


def load_config(env_file: str | None = None) -> Config:
    # Порядок: сначала .env, затем .env.test (перекрывает). Явный env_file — приоритетнее.
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / ".env.test", override=True)
    if env_file:
        load_dotenv(env_file, override=True)

    sources_raw = _load_yaml(CONFIG_DIR / "sources.yaml").get("sources", [])
    sources = []
    for item in sources_raw:
        if not isinstance(item, dict):
            raise ConfigError("sources.yaml: каждый источник должен быть словарём")
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if not name or not url:
            raise ConfigError("sources.yaml: у источника должны быть name и url")
        sources.append(Source(name=name, url=url))
    if not sources:
        raise ConfigError("sources.yaml: список источников пуст")

    formats = _load_yaml(CONFIG_DIR / "formats.yaml")
    weights = formats.get("weights", {})
    bad = formats.get("bad", [])
    sad = formats.get("sad", [])
    if not isinstance(weights, dict) or not weights:
        raise ConfigError("formats.yaml: weights должен быть непустым словарём")
    for key, value in weights.items():
        if not isinstance(key, str) or not isinstance(value, (int, float)):
            raise ConfigError("formats.yaml: weights должен быть словарём строка -> число")
    for key in ("bad", "sad"):
        items = formats.get(key, [])
        if not isinstance(items, list):
            raise ConfigError(f"formats.yaml: {key} должен быть списком")
        for item in items:
            if not isinstance(item, str):
                raise ConfigError(f"formats.yaml: {key} содержит не строку: {item!r}")

    settings_raw = _load_yaml(CONFIG_DIR / "settings.yaml")
    collect = settings_raw.get("collect", {})
    choose = settings_raw.get("choose", {})
    llm = settings_raw.get("llm", {})
    models = settings_raw.get("models", {})
    wiki = settings_raw.get("wiki", {})
    retry = settings_raw.get("retry", {})
    caption = settings_raw.get("caption", {})
    publish = settings_raw.get("publish", {})
    horizon = settings_raw.get("horizon", {})

    settings = Settings(
        collect_timeout=float(collect.get("timeout_seconds", 15)),
        snippet_length=int(collect.get("snippet_length", 300)),
        freshness_days=int(collect.get("freshness_days", 48)),
        top_n=int(collect.get("top_n", 25)),
        allow_stale_fallback=bool(collect.get("allow_stale_fallback", True)),
        user_agent=str(collect.get("user_agent", "KinoZa5Minut/1.0")),
        max_workers=int(collect.get("max_workers", 6)),
        window_size=int(choose.get("window_size", 10)),
        state_max_links=int(choose.get("state_max_links", 120)),
        state_max_signatures=int(choose.get("state_max_signatures", 60)),
        draft_temperature=float(llm.get("draft", {}).get("temperature", 0.75)),
        draft_max_tokens=int(llm.get("draft", {}).get("max_tokens", 1500)),
        draft_top_p=float(llm.get("draft", {}).get("top_p", 0.9)),
        judge_temperature=float(llm.get("judge", {}).get("temperature", 0.1)),
        judge_max_tokens=int(llm.get("judge", {}).get("max_tokens", 1300)),
        llm_timeout=float(llm.get("timeout_seconds", 60)),
        wiki_timeout=float(wiki.get("timeout_seconds", 15)),
        wiki_user_agent=str(wiki.get("user_agent", "KinoZa5Minut/1.0")),
        telegram_timeout=float(publish.get("timeout_seconds", 30)),
        retry_max_attempts=int(retry.get("max_attempts", 3)),
        retry_backoff=float(retry.get("backoff_seconds", 2)),
        caption_max_length=int(caption.get("max_length", 950)),
        caption_hard_limit=int(caption.get("hard_limit", 1000)),
        parse_mode=str(publish.get("parse_mode", "HTML")),
    )

    state_path = Path(os.getenv("STATE_PATH", "data/state.json"))
    if not state_path.is_absolute():
        state_path = ROOT / state_path

    stats_path = Path(os.getenv("STATS_PATH", "logs/format_stats.csv"))
    if not stats_path.is_absolute():
        stats_path = ROOT / stats_path

    source_mode = str(os.getenv("SOURCE_MODE", horizon.get("source", "rss"))).strip().lower()
    if source_mode not in ("rss", "horizon"):
        raise ConfigError(f"SOURCE_MODE должен быть rss или horizon, получено: {source_mode!r}")

    horizon_dir = Path(os.getenv("HORIZON_DIR", horizon.get("dir", "horizon")))
    if not horizon_dir.is_absolute():
        horizon_dir = ROOT / horizon_dir

    # summaries_dir по умолчанию — внутри клона Horizon (horizon_dir/data/summaries).
    summaries_default = str(horizon.get("summaries_dir", "")) or "data/summaries"
    horizon_summaries_dir = Path(os.getenv("HORIZON_SUMMARIES_DIR", summaries_default))
    if not horizon_summaries_dir.is_absolute():
        horizon_summaries_dir = horizon_dir / horizon_summaries_dir

    horizon_run = bool(horizon.get("run", True))
    horizon_hours = int(horizon.get("hours", 24))
    horizon_timeout = float(horizon.get("timeout_seconds", 600))

    draft_model = _optional_env("DRAFT_MODEL") or str(models.get("draft", ""))
    judge_model = _optional_env("JUDGE_MODEL") or str(models.get("judge", ""))
    judge_fallback_model = _optional_env("JUDGE_FALLBACK_MODEL")

    return Config(
        sources=sources,
        weights={str(k): int(v) for k, v in weights.items()},
        bad=[str(x) for x in bad],
        sad=[str(x) for x in sad],
        settings=settings,
        llm_base_url=_require_env("LLM_BASE_URL"),
        llm_api_key=_require_env("LLM_API_KEY"),
        draft_model=draft_model,
        judge_model=judge_model,
        judge_fallback_model=judge_fallback_model,
        bot_token=_require_env("BOT_TOKEN"),
        channel_chat_id=_optional_env("CHANNEL_CHAT_ID"),
        admin_chat_id=_optional_env("ADMIN_CHAT_ID"),
        proxy_url=_optional_env("PROXY_URL") or None,
        state_path=state_path,
        stats_path=stats_path,
        source_mode=source_mode,
        horizon_dir=horizon_dir,
        horizon_summaries_dir=horizon_summaries_dir,
        horizon_run=horizon_run,
        horizon_hours=horizon_hours,
        horizon_timeout=horizon_timeout,
    )