"""Загрузка конфигурации диспетчера.

Два источника клиентов, оба живые:

1. ``.env`` — ``CLIENT_<user_id>=<token>``. Мастер вписывает руками.
   Осталось для совместимости и для случая «клиент не может вставить сам».
2. ``clients.db`` → ``bot_tokens`` — клиент активировал бота через главного
   бота (``pro_code/activation.py``). Токен там зашифрован.

При совпадении user_id побеждает БД: клиент мог перевыпустить токен, и
свежая запись важнее старой строки в .env.

Главный бот (``MASTER_BOT_TOKEN``) — бот мастера, через него клиенты
активируют своих. Он не входит в ``clients``.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]

#: Префикс для переменных CLIENT_ в .env
CLIENT_PREFIX = "CLIENT_"

#: Токен главного бота (мастера).
ENV_MASTER_TOKEN = "MASTER_BOT_TOKEN"


class ConfigError(Exception):
    """Ошибка конфигурации диспетчера."""


@dataclass
class ClientBot:
    """Один клиентский бот."""
    user_id: int          # Telegram ID клиента
    token: str            # токен бота из BotFather
    plan: str             # free | auto
    active: bool = True   # прошёл ли getMe
    source: str = "env"   # env | db — откуда взят токен
    bot_username: str | None = None


@dataclass
class Config:
    admin_chat_id: int
    clients: list[ClientBot] = field(default_factory=list)
    #: Токен главного бота. None — главный бот не поднимается.
    master_token: str | None = None


def _detect_plan(user_id: int) -> str:
    """Определяет план клиента из переменной CLIENT_<id>_PLAN, иначе free."""
    key = f"{CLIENT_PREFIX}{user_id}_PLAN"
    val = os.environ.get(key, "free").strip().lower()
    return val if val in ("free", "auto") else "free"


def _clients_from_env() -> dict[int, ClientBot]:
    """Клиенты из .env: CLIENT_<user_id>=<token>."""
    clients: dict[int, ClientBot] = {}
    pat = re.compile(rf"^{CLIENT_PREFIX}(\d+)$")
    for key, value in os.environ.items():
        m = pat.match(key)
        if not m or not value.strip():
            continue
        try:
            user_id = int(m.group(1))
        except ValueError:
            continue
        clients[user_id] = ClientBot(
            user_id=user_id,
            token=value.strip(),
            plan=_detect_plan(user_id),
            source="env",
        )
    return clients


def _clients_from_db() -> dict[int, ClientBot]:
    """Клиенты из clients.db → bot_tokens (токены расшифровываются).

    БД может отсутствовать или быть без ключа шифрования — это не повод
    не стартовать: клиенты из .env работают и без неё.
    """
    try:
        from pro_code.clients_db import ClientsDb

        db = ClientsDb()
    except Exception as exc:
        logger.warning("clients.db недоступна, клиенты только из .env: %s", exc)
        return {}

    try:
        clients: dict[int, ClientBot] = {}
        for bot in db.list_active_bots():
            user_id = bot["user_id"]
            row = db.get_client(user_id)
            plan = (row or {}).get("plan", "free")
            clients[user_id] = ClientBot(
                user_id=user_id,
                token=bot["token"],
                plan=plan if plan in ("free", "auto") else "free",
                source="db",
                bot_username=bot.get("bot_username"),
            )
        return clients
    except Exception as exc:
        logger.warning("не удалось прочитать токены из БД: %s", exc)
        return {}
    finally:
        db.close()


def load_config() -> Config:
    """Читает .env и clients.db, собирает конфиг.

    Формат .env:
      ADMIN_CHAT_ID=...
      MASTER_BOT_TOKEN=...            (опционально)
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

    merged = _clients_from_env()
    from_db = _clients_from_db()
    for user_id, bot in from_db.items():
        if user_id in merged:
            logger.info(
                "клиент %d: токен из БД перекрывает .env", user_id
            )
        merged[user_id] = bot

    clients = [merged[uid] for uid in sorted(merged)]

    master_token = os.environ.get(ENV_MASTER_TOKEN, "").strip() or None

    if not clients and not master_token:
        raise ConfigError(
            "нет ни одного бота: ни CLIENT_<user_id>=<token> в .env, "
            "ни активированного бота в clients.db, ни MASTER_BOT_TOKEN"
        )

    return Config(
        admin_chat_id=admin_chat_id,
        clients=clients,
        master_token=master_token,
    )
