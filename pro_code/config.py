"""Загрузка конфигурации диспетчера: .env → токены клиентов, admin_chat_id."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

#: Префикс для переменных CLIENT_ в .env
CLIENT_PREFIX = "CLIENT_"


class ConfigError(Exception):
    """Ошибка конфигурации диспетчера."""


@dataclass
class ClientBot:
    """Один клиентский бот."""
    user_id: int          # Telegram ID клиента
    token: str            # токен бота из BotFather
    plan: str             # free | auto
    active: bool = True   # прошёл ли getMe


@dataclass
class Config:
    admin_chat_id: int
    clients: list[ClientBot] = field(default_factory=list)


def _detect_plan(user_id: int) -> str:
    """Определяет план клиента из переменной CLIENT_<id>_PLAN, иначе free."""
    key = f"{CLIENT_PREFIX}{user_id}_PLAN"
    val = os.environ.get(key, "free").strip().lower()
    return val if val in ("free", "auto") else "free"


def load_config() -> Config:
    """Читает .env, собирает конфиг.

    Формат:
      ADMIN_CHAT_ID=...
      CLIENT_<user_id>=<token>
      CLIENT_<user_id>_PLAN=free|auto (опционально)
    """
    load_dotenv(ROOT / ".env")

    raw_admin = os.environ.get("ADMIN_CHAT_ID", "").strip()
    if not raw_admin:
        raise ConfigError("ADMIN_CHAT_ID не задан в .env")
    try:
        admin_chat_id = int(raw_admin)
    except ValueError:
        raise ConfigError(f"ADMIN_CHAT_ID не число: {raw_admin}")

    clients: list[ClientBot] = []
    pat = re.compile(rf"^{CLIENT_PREFIX}(\d+)$")

    for key, value in os.environ.items():
        m = pat.match(key)
        if not m or not value.strip():
            continue
        try:
            user_id = int(m.group(1))
        except ValueError:
            continue
        plan = _detect_plan(user_id)
        clients.append(ClientBot(
            user_id=user_id,
            token=value.strip(),
            plan=plan,
        ))

    if not clients:
        raise ConfigError("В .env не найдено ни одного CLIENT_<user_id>=<token>")

    return Config(admin_chat_id=admin_chat_id, clients=clients)
