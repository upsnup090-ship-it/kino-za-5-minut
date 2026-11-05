"""Управление токенами клиентов: приём user_id:token, проверка getMe."""

from __future__ import annotations

import logging
import re
import secrets
from typing import Any

from pro_code.tg_api import get_me, TgError
from rrA.clients import verify_client_token

logger = logging.getLogger(__name__)

#: Формат: user_id:token (user_id — число, token — из BotFather)
TOKEN_PATTERN = re.compile(r"^(\d+):(.+)$")

#: Символы для ключа активации (без похожих 0/O, 1/I, 8/B).
_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_key() -> str:
    """Сгенерировать случайный ключ активации вида KINO-XXXX-XXXX-XXXX."""
    def _block() -> str:
        return "".join(secrets.choice(_KEY_ALPHABET) for _ in range(4))
    return f"KINO-{_block()}-{_block()}-{_block()}"


def parse_token_input(raw: str) -> dict[str, Any] | None:
    """Разбирает строку user_id:token. Возвращает {user_id, token} или None."""
    m = TOKEN_PATTERN.match(raw.strip())
    if not m:
        return None
    try:
        user_id = int(m.group(1))
    except ValueError:
        return None
    token = m.group(2)
    if len(token) < 10:  # минимальная длина токена
        return None
    return {"user_id": user_id, "token": token}


def register_token(
    user_id: int,
    token: str,
    current_env: dict[str, str],
) -> dict[str, Any]:
    """Регистрирует токен: проверяет getMe и возвращает информацию.

    current_env — текущие переменные окружения (из os.environ).
    Возвращает dict с результатом.
    """
    bot_info = verify_client_token(token)
    if bot_info is None:
        return {"ok": False, "error": "Токен не прошёл проверку (getMe). Бот не существует или токен бит."}

    env_key = f"CLIENT_{user_id}"
    if env_key in current_env:
        old_token = current_env[env_key]
        if old_token == token:
            return {"ok": True, "info": bot_info, "note": "Токен уже зарегистрирован."}
        return {"ok": True, "info": bot_info, "note": f"Токен заменён (старый бот будет деактивирован)."}

    return {
        "ok": True,
        "info": bot_info,
        "note": "Новый клиент добавлен. Пропиши в .env и перезапусти диспетчер.",
    }
