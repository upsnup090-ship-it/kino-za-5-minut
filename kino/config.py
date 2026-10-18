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
    reddit_pause: float = 3.0
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
    reddit_max_attempts: int = 2
    caption_max_length: int = 950
    caption_hard_limit: int = 1000
    parse_mode: str = "HTML"
    post_variations: dict[str, int] = field(default_factory=dict)
    deadline_seconds: float = 180.0
    # --- расписание 24/7 ---
    schedule_windows: list[str] = field(default_factory=list)
    max_posts_per_day: int = 3
    min_gap_minutes: int = 45
    timezone: str = "Europe/Moscow"
    tick_seconds: int = 60
    # --- логи ---
    log_level: str = "INFO"
    log_file: str = "logs/kino.log"
    log_max_bytes: int = 5 * 1024 * 1024
    log_backup_count: int = 5
    log_console: bool = True
    log_retention_days: int = 14
    log_max_total_bytes: int = 100 * 1024 * 1024
    log_purge_interval_hours: int = 24


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
    llm_proxy_url: str | None = None
    state_path: Path = ROOT / "data" / "state.json"
    stats_path: Path = ROOT / "logs" / "format_stats.csv"
    cache_dir: Path = ROOT / "data" / "feed_cache"
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


def _env_is_set(name: str) -> bool:
    """Переменная присутствует в окружении (в т.ч. пустой строкой).

    Нужна там, где пустое значение — это явный выбор «выключено», а не
    «не задано»: так LLM_PROXY_URL= отключает фолбэк на PROXY_URL.
    """
    return name in os.environ


def _resolve_llm_proxy() -> str | None:
    """Прокси для LLM-запросов.

    LLM_PROXY_URL задан непустым — используем его. LLM_PROXY_URL задан пустым —
    LLM идёт напрямую (явное отключение). Не задан вовсе — фолбэк на PROXY_URL.
    """
    if _env_is_set("LLM_PROXY_URL"):
        return _optional_env("LLM_PROXY_URL") or None
    return _optional_env("PROXY_URL") or None


def load_config(env_file: str | None = None) -> Config:
    # Без явного env_file: .env, затем .env.test (перекрывает).
    # С явным env_file он единственный источник — иначе тесты и альтернативные
    # окружения не изолируются от боевого .env.test.
    if env_file:
        os.environ.setdefault("ENV_FILE", str(env_file))
        load_dotenv(env_file, override=True)
    else:
        load_dotenv(ROOT / ".env")
        load_dotenv(ROOT / ".env.test", override=True)

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
    run = settings_raw.get("run", {})
    schedule = settings_raw.get("schedule", {})
    logging_cfg = settings_raw.get("logging", {})
    horizon = settings_raw.get("horizon", {})

    settings = Settings(
        collect_timeout=float(collect.get("timeout_seconds", 15)),
        snippet_length=int(collect.get("snippet_length", 300)),
        freshness_days=int(collect.get("freshness_days", 48)),
        top_n=int(collect.get("top_n", 25)),
        allow_stale_fallback=bool(collect.get("allow_stale_fallback", True)),
        user_agent=str(collect.get("user_agent", "KinoZa5Minut/1.0")),
        max_workers=int(collect.get("max_workers", 6)),
        reddit_pause=float(collect.get("reddit_pause_seconds", 3)),
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
        reddit_max_attempts=int(retry.get("reddit_max_attempts", 2)),
        retry_backoff=float(retry.get("backoff_seconds", 2)),
        caption_max_length=int(caption.get("max_length", 950)),
        caption_hard_limit=int(caption.get("hard_limit", 1000)),
        parse_mode=str(publish.get("parse_mode", "HTML")),
        post_variations=dict(settings_raw.get("post_variations", {}) or {}),
        deadline_seconds=float(run.get("deadline_seconds", 180)),
        schedule_windows=[str(x) for x in schedule.get("windows", [])],
        max_posts_per_day=int(schedule.get("max_posts_per_day", 3)),
        min_gap_minutes=int(schedule.get("min_gap_minutes", 45)),
        timezone=str(schedule.get("timezone", "Europe/Moscow")),
        tick_seconds=max(1, int(schedule.get("tick_seconds", 60))),
        log_level=str(logging_cfg.get("level", "INFO")),
        log_file=str(logging_cfg.get("file", "logs/kino.log")),
        log_max_bytes=int(logging_cfg.get("max_bytes", 5 * 1024 * 1024)),
        log_backup_count=int(logging_cfg.get("backup_count", 5)),
        log_console=bool(logging_cfg.get("console", True)),
        log_retention_days=int(logging_cfg.get("retention_days", 14)),
        log_max_total_bytes=int(logging_cfg.get("max_total_bytes", 100 * 1024 * 1024)),
        log_purge_interval_hours=int(logging_cfg.get("purge_interval_hours", 24)),
    )

    # Валидация расписания: окна парсятся через schedule.parse_windows, мусор
    # падает на старте (иначе serve молчал бы сутки из-за опечатки). Зона —
    # тоже проверяется здесь, а не при первом посте.
    if settings.schedule_windows:
        try:
            from kino.schedule import ScheduleConfig, parse_windows

            parsed = parse_windows(settings.schedule_windows)
            schedule_cfg = ScheduleConfig(
                windows=parsed,
                max_posts_per_day=settings.max_posts_per_day,
                min_gap_minutes=settings.min_gap_minutes,
                tz=settings.timezone,
            )
            # Доступ к .zone проверяет таймзону сразу, а не при первом посте:
            # негодная зона падает на старте, а не «serve молчит сутки».
            _ = schedule_cfg.zone
        except Exception as exc:  # noqa: BLE001 — любая ошибка расписания → ConfigError
            raise ConfigError(f"неверное расписание в settings.yaml: {exc}") from exc

    state_path = Path(os.getenv("STATE_PATH", "data/state.json"))
    if not state_path.is_absolute():
        state_path = ROOT / state_path

    stats_path = Path(os.getenv("STATS_PATH", "logs/format_stats.csv"))
    if not stats_path.is_absolute():
        stats_path = ROOT / stats_path

    cache_dir = Path(os.getenv("FEED_CACHE_DIR", "data/feed_cache"))
    if not cache_dir.is_absolute():
        cache_dir = ROOT / cache_dir

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

    # Выбор провайдера LLM: env > yaml, по умолчанию gemini (free tier).
    provider_name = _optional_env("LLM_PROVIDER") or str(settings_raw.get("llm_provider", "gemini"))
    providers = settings_raw.get("providers", {})
    provider = providers.get(provider_name, {}) if isinstance(providers, dict) else {}

    provider_base_url = provider.get("base_url", "") if isinstance(provider, dict) else ""
    provider_api_key = provider.get("api_key", "") if isinstance(provider, dict) else ""
    provider_draft_model = provider.get("draft_model", "") if isinstance(provider, dict) else ""
    provider_judge_model = provider.get("judge_model", "") if isinstance(provider, dict) else ""

    # API key в YAML может быть плейсхолдером ${VAR} — разрешаем через env.
    def _resolve_api_key(key_value: str) -> str:
        if isinstance(key_value, str) and key_value.startswith("${") and key_value.endswith("}"):
            return os.getenv(key_value[2:-1], "")
        return key_value or ""

    llm_base_url = _optional_env("LLM_BASE_URL") or provider_base_url
    llm_api_key = _optional_env("LLM_API_KEY") or _resolve_api_key(provider_api_key)
    draft_model = _optional_env("DRAFT_MODEL") or provider_draft_model or str(models.get("draft", ""))
    judge_model = _optional_env("JUDGE_MODEL") or provider_judge_model or str(models.get("judge", ""))
    judge_fallback_model = _optional_env("JUDGE_FALLBACK_MODEL")

    if not llm_base_url:
        raise ConfigError("LLM_BASE_URL не задан (ни в .env, ни в providers.<name>.base_url)")
    if not llm_api_key:
        raise ConfigError("LLM_API_KEY не задан (ни в .env, ни в providers.<name>.api_key)")

    return Config(
        sources=sources,
        weights={str(k): int(v) for k, v in weights.items()},
        bad=[str(x) for x in bad],
        sad=[str(x) for x in sad],
        settings=settings,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        draft_model=draft_model,
        judge_model=judge_model,
        judge_fallback_model=judge_fallback_model,
        bot_token=_require_env("BOT_TOKEN"),
        channel_chat_id=_optional_env("CHANNEL_CHAT_ID"),
        admin_chat_id=_optional_env("ADMIN_CHAT_ID"),
        proxy_url=_optional_env("PROXY_URL") or None,
        llm_proxy_url=_resolve_llm_proxy(),
        state_path=state_path,
        stats_path=stats_path,
        cache_dir=cache_dir,
        source_mode=source_mode,
        horizon_dir=horizon_dir,
        horizon_summaries_dir=horizon_summaries_dir,
        horizon_run=horizon_run,
        horizon_hours=horizon_hours,
        horizon_timeout=horizon_timeout,
    )