"""Активация бота клиента через главного бота.

Поток:

    клиент → /start главному боту
      → бот просит токен
      → клиент вставляет токен из @BotFather
      → getMe проверяет токен
      → токен шифруется и ложится в clients.db → bot_tokens
      → бот клиента поднимается сразу, без рестарта диспетчера

Состояние диалога живёт в памяти процесса (``_pending``): это короткий
шаг, рестарт посреди него не страшен — клиент повторит /start.

Токен в лог не пишется никогда, только маска (``crypto.mask``).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from pro_code.clients_db import ClientsDb
from pro_code.crypto import CryptoError, mask
from pro_code.config import _detect_plan
from pro_code.tg_api import TgError, get_me, inline_keyboard, send_message

logger = logging.getLogger(__name__)

#: Состояния активации.
STATE_IDLE = "idle"
STATE_AWAIT_TOKEN = "await_token"

#: Токен BotFather: <цифры>:<35+ символов base64url-подобных>.
TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")

#: Кто ждёт ввода токена: user_id → True.
_pending: dict[int, bool] = {}

#: Колбэк запуска бота: (user_id, token) → None. Ставит диспетчер.
_spawn: Callable[[int, str], None] | None = None


def set_spawner(fn: Callable[[int, str], None] | None) -> None:
    """Диспетчер регистрирует здесь запуск бота без рестарта."""
    global _spawn
    _spawn = fn


def is_pending(user_id: int) -> bool:
    """Ждём ли от этого пользователя токен."""
    return _pending.get(user_id, False)


def reset(user_id: int) -> None:
    """Сбросить состояние активации."""
    _pending.pop(user_id, None)


def _send(token: str, chat_id: int, text: str, **kwargs: Any) -> None:
    try:
        send_message(token, chat_id, text, **kwargs)
    except TgError as exc:
        logger.warning("не удалось ответить в чат %s: %s", chat_id, exc)


def start(
    master_token: str,
    chat_id: int,
    user_id: int,
    clients_db: ClientsDb,
) -> None:
    """Клиент нажал /start у главного бота."""
    existing = clients_db.get_bot_info(user_id)
    if existing and existing.get("active"):
        username = existing.get("bot_username") or "твой бот"
        _send(
            master_token, chat_id,
            f"✅ У тебя уже активирован бот @{username}.\n\n"
            "Если хочешь заменить токен — отправь новый.",
            reply_markup=inline_keyboard([
                [("🔄 Заменить токен", "cb:/replace_token")],
            ]),
        )
        return

    _pending[user_id] = True
    _send(
        master_token, chat_id,
        "👋 **Добро пожаловать в Киномодули**\n\n"
        "Чтобы начать, нужен свой бот.\n\n"
        "**Как получить токен:**\n"
        "1. Открой @BotFather\n"
        "2. Отправь `/newbot`\n"
        "3. Придумай имя и username (должен заканчиваться на `bot`)\n"
        "4. BotFather пришлёт токен вида `1234567890:AAH...`\n\n"
        "Вставь его сюда одним сообщением.",
        reply_markup=inline_keyboard([[("❌ Отмена", "cb:/cancel")]]),
    )


def handle(
    master_token: str,
    chat_id: int,
    user_id: int,
    text: str,
    clients_db: ClientsDb,
) -> bool:
    """Обработать сообщение в контексте активации.

    Возвращает True, если сообщение поглощено.
    """
    cmd = text.strip().lower()

    if cmd in ("/start", "start", "🚀 начать"):
        start(master_token, chat_id, user_id, clients_db)
        return True

    if cmd in ("/my_bots", "📋 мои боты", "мои боты"):
        my_bots(master_token, chat_id, user_id, clients_db)
        return True

    if cmd in ("/cancel", "отмена", "❌ отмена"):
        reset(user_id)
        _send(master_token, chat_id, "❌ Активация отменена. `/start` — начать заново.")
        return True

    if cmd in ("/replace_token", "🔄 заменить токен"):
        _pending[user_id] = True
        _send(
            master_token, chat_id,
            "Отправь новый токен из @BotFather одним сообщением.",
            reply_markup=inline_keyboard([[("❌ Отмена", "cb:/cancel")]]),
        )
        return True

    if not is_pending(user_id):
        return False

    return _on_token(master_token, chat_id, user_id, text.strip(), clients_db)


def _on_token(
    master_token: str,
    chat_id: int,
    user_id: int,
    raw: str,
    clients_db: ClientsDb,
) -> bool:
    """Проверить и сохранить токен."""
    if not TOKEN_RE.match(raw):
        _send(
            master_token, chat_id,
            "❌ Это не похоже на токен бота.\n\n"
            "Токен выглядит так: `1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw`\n"
            "Скопируй его из @BotFather целиком и отправь ещё раз.",
            reply_markup=inline_keyboard([[("❌ Отмена", "cb:/cancel")]]),
        )
        return True

    # Проверка токена у Telegram
    try:
        info = get_me(raw)
    except TgError as exc:
        logger.warning("токен user_id=%d отклонён Telegram: %s", user_id, exc)
        _send(
            master_token, chat_id,
            "❌ Telegram не принял этот токен.\n\n"
            "Проверь, что скопировал целиком и что бот не удалён в @BotFather.\n"
            "Отправь токен ещё раз или `/cancel`.",
            reply_markup=inline_keyboard([[("❌ Отмена", "cb:/cancel")]]),
        )
        return True

    bot_id = info.get("id")
    bot_username = info.get("username")

    # Сохранение (шифрование внутри). План не затираем: если клиент уже
    # есть в БД (мастер задал план auto через .env или RootA) — сохраняем
    # его план, иначе free.
    try:
        existing_row = clients_db.get_client(user_id)
        current_plan = (existing_row or {}).get("plan") if existing_row else None
        if current_plan not in ("free", "auto"):
            current_plan = _detect_plan(user_id)  # из CLIENT_<id>_PLAN в .env
        clients_db.upsert_client(user_id, plan=current_plan, status="active")
        clients_db.set_bot_token(
            user_id, raw, bot_id=bot_id, bot_username=bot_username
        )
    except CryptoError as exc:
        logger.error("токен user_id=%d не сохранён: %s", user_id, exc)
        _send(
            master_token, chat_id,
            "❌ Не удалось сохранить токен: на сервере не настроен ключ "
            "шифрования. Сообщи мастеру.",
        )
        return True
    except Exception as exc:
        logger.exception("ошибка сохранения токена user_id=%d: %s", user_id, exc)
        _send(master_token, chat_id, "❌ Ошибка сохранения. Попробуй ещё раз.")
        return True

    reset(user_id)
    logger.info(
        "бот активирован: user_id=%d, @%s (id=%s), токен %s",
        user_id, bot_username, bot_id, mask(raw),
    )

    # Поднимаем бота сразу
    started = False
    if _spawn is not None:
        try:
            _spawn(user_id, raw)
            started = True
        except Exception as exc:
            logger.exception("не удалось запустить бота user_id=%d: %s", user_id, exc)

    lines = [
        f"✅ **Бот @{bot_username} активирован!**\n",
        f"Токен сохранён в зашифрованном виде ({mask(raw)}).",
        "",
    ]
    if started:
        lines.append("Бот уже работает — открой его и нажми `/start`.")
    else:
        lines.append(
            "Бот запустится в течение минуты. Открой его и нажми `/start`."
        )
    lines.append("")
    lines.append("Дальше всё — в твоём боте: каналы, постинг, статистика.")

    _send(
        master_token, chat_id, "\n".join(lines),
        reply_markup=inline_keyboard([
            [("📋 Мои боты", "cb:/my_bots")],
        ]),
    )
    return True


def my_bots(master_token: str, chat_id: int, user_id: int, clients_db: ClientsDb) -> None:
    """Показать ботов клиента."""
    info = clients_db.get_bot_info(user_id)
    if not info:
        _send(
            master_token, chat_id,
            "У тебя пока нет активированных ботов.",
            reply_markup=inline_keyboard([[("➕ Активировать", "cb:/start")]]),
        )
        return

    status = "✅ активен" if info.get("active") else "⏸ отключён"
    client = clients_db.get_client(user_id) or {}
    plan = client.get("plan", "free")
    _send(
        master_token, chat_id,
        f"📋 **Твой бот**\n\n"
        f"@{info.get('bot_username') or '—'}\n"
        f"Статус: {status}\n"
        f"План: **{plan}**\n"
        f"Активирован: {info.get('added_at', '')[:10]}",
        reply_markup=inline_keyboard([
            [("🔄 Заменить токен", "cb:/replace_token")],
        ]),
    )
