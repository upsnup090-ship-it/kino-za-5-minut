"""Маршрутизация апдейтов от ботов клиентов по модулям.

Диспетчер получает апдейт → router определяет user_id и план → направляет
в нужный модуль или возвращает экран-заглушку.
"""

from __future__ import annotations

import logging
from typing import Any

from pro_code.clients_db import ClientsDb
from pro_code.tg_api import send_message
from pro_code.info import ABOUT_TEXT, FAQ_TEXT
from project_ruch.ruch_db import RuchDb
from rrA.admin_router import handle_owner_command

logger = logging.getLogger(__name__)

#: Заглушки для модулей (пока не реализованы)
MODULES_PLACEHOLDER = {
    "repeating": "🔁 **Повторяющиеся сообщения**\n\nРаздел в разработке.",
    "manual": "✍️ **Ручной постинг**\n\nРаздел в разработке.",
    "stats": "📊 **Статистика**\n\nРаздел в разработке.",
    "backup": "💾 **Бэкап**\n\nРаздел в разработке.",
    "autoposting": "🤖 **Автопостинг**\n\nРаздел в разработке. Доступно по плану auto.",
    "rootA": "👥 **RootA (управление клиентами)**\n\nРаздел в разработке. Только для мастера.",
}


def build_main_menu(plan: str, is_owner: bool = False) -> str:
    """Главное меню для клиента."""
    lines = ["🏠 **Главное меню**\n"]
    lines.append("🔁 Повторяющиеся сообщения")
    lines.append("✍️ Ручной постинг")
    lines.append("📊 Статистика")
    lines.append("ℹ️ Инфо")
    if plan == "auto":
        lines.append("🤖 Автопостинг (auto)")
    if is_owner:
        lines.append("")
        lines.append("👥 RootA (только мастер)")
        lines.append("💾 Бэкап+Логи (только мастер)")
    lines.append("")
    lines.append("⚙️ Настройки")
    return "\n".join(lines)


def handle_message(
    token: str,
    user_id: int,
    plan: str,
    is_owner: bool,
    chat_id: int,
    text: str,
    clients_db: ClientsDb | None = None,
    ruch_db: RuchDb | None = None,
) -> None:
    """Обработать текстовое сообщение от клиента."""
    cmd = text.strip().lower()

    # --- команды owner перенаправляем в admin_router ---
    if is_owner:
        # Проверяем, не обычная ли это команда из меню
        owner_cmds = ("/clients", "/stats", "/backup", "/backups", "/logs", "/register", "/client")
        if any(cmd.startswith(c) for c in owner_cmds) or cmd in ("/start", "/menu", "/help"):
            if clients_db and ruch_db:
                handle_owner_command(token, chat_id, text, clients_db, ruch_db)
                return

    # --- команды ---
    if cmd == "/start":
        reply = (
            "👋 Привет!\n\n"
            "Твой бот активирован. Используй меню ниже.\n\n"
            + build_main_menu(plan, is_owner)
        )
        send_message(token, chat_id, reply)
        return

    if cmd in ("/menu", "/help"):
        send_message(token, chat_id, build_main_menu(plan, is_owner))
        return

    if cmd == "/info":
        send_message(token, chat_id, ABOUT_TEXT)
        return

    if cmd == "/faq":
        send_message(token, chat_id, FAQ_TEXT)
        return

    # --- модули-заглушки ---
    if cmd in ("🔁 повторяющиеся", "повторяющиеся", "repeating"):
        send_message(token, chat_id, MODULES_PLACEHOLDER["repeating"])
        return
    if cmd in ("✍️ ручной постинг", "ручной постинг", "manual", "✍️"):
        send_message(token, chat_id, MODULES_PLACEHOLDER["manual"])
        return
    if cmd in ("📊 статистика", "статистика", "stats", "📊"):
        send_message(token, chat_id, MODULES_PLACEHOLDER["stats"])
        return
    if cmd in ("ℹ️ инфо", "инфо", "info", "ℹ️"):
        send_message(token, chat_id, ABOUT_TEXT)
        return
    if cmd in ("💾 бэкап", "бэкап", "backup", "💾"):
        send_message(token, chat_id, MODULES_PLACEHOLDER["backup"])
        return
    if cmd in ("🤖 автопостинг", "автопостинг", "autoposting", "🤖"):
        if plan != "auto":
            send_message(token, chat_id, "❌ Автопостинг доступен только по плану **auto**.")
            return
        send_message(token, chat_id, MODULES_PLACEHOLDER["autoposting"])
        return
    if cmd in ("👥 roota", "roota", "клиенты", "👥"):
        if not is_owner:
            send_message(token, chat_id, "❌ Нет доступа.")
            return
        # Перенаправляем в admin_router
        if clients_db and ruch_db:
            handle_owner_command(token, chat_id, "/menu", clients_db, ruch_db)
        return

    # --- неизвестная команда ---
    send_message(
        token, chat_id,
        "Я понимаю только команды из меню. Отправь /menu.",
    )


def handle_no_token(chat_id: int, token: str | None = None) -> None:
    """Ответ бота клиенту без токена (не активирован)."""
    # Если токена нет — отправить нечем (бота не существует)
    # Если бот есть, но клиент не привязан — отправляем отказ+инфо
    if token:
        send_message(
            token, chat_id,
            "❌ **Токен не активирован.**\n\n"
            "Для использования бота нужен токен доступа.\n"
            "Обратись к мастеру для получения токена.\n\n"
            "ℹ️ Используй /info для справки о боте.",
        )
