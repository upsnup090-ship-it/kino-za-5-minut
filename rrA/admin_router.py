"""Маршрутизация команд owner (RootA) из чата мастера.

Подключается к pro_code/router.py: если апдейт от admin_chat_id,
команды уходят сюда вместо обычного меню.
"""

from __future__ import annotations

import logging
from typing import Any

from pro_code.clients_db import ClientsDb
from pro_code.tg_api import send_message
from project_ruch.ruch_db import RuchDb
from rrA.backup_logs import list_backups, make_backup, export_logs
from rrA.clients import list_clients, get_client, add_client, set_plan, set_status, get_bindings, verify_client_token
from rrA.stats import full_owner_report
from rrA.tokens import parse_token_input, register_token

logger = logging.getLogger(__name__)

#: Команды owner — для маршрутизации
OWNER_COMMANDS = (
    "/clients", "/stats", "/backup", "/backups", "/logs",
    "/register", "/client", "/llm_status", "/reset_llm", "/set_llm_limit",
)


def handle_owner_command(
    token: str,
    chat_id: int,
    text: str,
    clients_db: ClientsDb,
    ruch_db: RuchDb,
) -> None:
    """Обрабатывает команду owner."""
    cmd = text.strip().lower()

    if cmd in ("/start", "/menu", "🏠 главное меню"):
        _send_main_menu(token, chat_id)
        return

    if cmd in ("/clients", "👥 клиенты", "clients"):
        _show_clients(token, chat_id, clients_db)
        return

    if cmd in ("/stats", "📊 статистика", "stats"):
        _show_stats(token, chat_id, clients_db, ruch_db)
        return

    if cmd in ("/backup", "💾 бэкап", "backup"):
        _do_backup(token, chat_id)
        return

    if cmd in ("/backups", "💾 список бэкапов", "backups"):
        _list_backups(token, chat_id)
        return

    if cmd in ("/logs", "📜 логи", "logs"):
        _show_logs(token, chat_id)
        return

    # LLM-лимит
    if cmd.startswith("/llm_status"):
        _show_llm_status(token, chat_id, clients_db)
        return
    if cmd.startswith("/reset_llm "):
        _reset_llm_cmd(token, chat_id, text[11:], clients_db)
        return
    if cmd.startswith("/set_llm_limit "):
        send_message(token, chat_id, "Лимит фиксирован: $1. Для изменения правь конфиг.")
        return

    # Регистрация токена
    if cmd.startswith("/register "):
        _register_token_cmd(token, chat_id, text[10:], clients_db)
        return

    # Инфо о клиенте
    if cmd.startswith("/client "):
        _show_client_info(token, chat_id, text[8:], clients_db)
        return

    send_message(token, chat_id, "Неизвестная команда. Отправь /menu для списка.")


def _send_main_menu(token: str, chat_id: int) -> None:
    menu = (
        "🏠 **Главное меню (RootA)**\n\n"
        "👥 /clients — список клиентов\n"
        "📊 /stats — полная статистика\n"
        "💾 /backup — создать бэкап\n"
        "💾 /backups — список бэкапов\n"
        "📜 /logs — просмотр логов\n"
        "📝 /register user_id:token — добавить/сменить токен\n"
        "👤 /client user_id — информация о клиенте\n"
        "💰 /llm_status — статус LLM-расхода клиентов\n"
        "🔄 /reset_llm user_id — сбросить LLM-расход клиента\n"
    )
    send_message(token, chat_id, menu)


def _show_clients(token: str, chat_id: int, clients_db: ClientsDb) -> None:
    clients = list_clients(clients_db)
    if not clients:
        send_message(token, chat_id, "Нет клиентов.")
        return
    lines = ["👥 **Клиенты**\n"]
    for c in clients:
        spent = clients_db.get_llm_spent(c["user_id"])
        lines.append(
            f"  `{c['user_id']}` | {c.get('plan', 'free')} | {c.get('status', 'active')} | ${spent:.4f}"
        )
    send_message(token, chat_id, "\n".join(lines))


def _show_stats(token: str, chat_id: int, clients_db: ClientsDb, ruch_db: RuchDb) -> None:
    stats = full_owner_report(clients_db, ruch_db)
    lines = [
        "📊 **Полная статистика**\n",
        f"Клиентов: {stats['total_clients']}",
        f"  активных: {stats['active_clients']}",
        f"  неактивных: {stats['inactive_clients']}",
        f"Ботов: {stats['total_bots']}",
        f"Привязок (чаты/каналы): {stats['total_bindings']}",
        "",
        "По типам:",
    ]
    for t, cnt in stats.get("by_type", {}).items():
        lines.append(f"  {t}: {cnt}")
    lines.append("")
    lines.append("По планам:")
    for p, cnt in stats.get("by_plan", {}).items():
        lines.append(f"  {p}: {cnt}")
    send_message(token, chat_id, "\n".join(lines))


def _do_backup(token: str, chat_id: int) -> None:
    send_message(token, chat_id, "💾 Создаю бэкап...")
    result = make_backup()
    if result["ok"]:
        send_message(token, chat_id, f"✅ Бэкап создан: `{result['filename']}` ({result['size_bytes']} байт)")
    else:
        send_message(token, chat_id, f"❌ Ошибка: {result.get('error', 'неизвестна')}")


def _list_backups(token: str, chat_id: int) -> None:
    backups = list_backups()
    if not backups:
        send_message(token, chat_id, "Бэкапов пока нет.")
        return
    lines = ["💾 **Список бэкапов**\n"]
    for b in backups:
        lines.append(f"  `{b['filename']}` — {b['size_bytes']} байт")
    send_message(token, chat_id, "\n".join(lines))


def _show_logs(token: str, chat_id: int) -> None:
    lines = [
        "📜 **Логи**\n",
        "`_agent_log.md` — журнал агента",
        "`project_ruch/logs/*.log` — логи бота",
        "",
        "Пока доступен только просмотр через чтение файлов.",
        "Экспорт — в разработке.",
    ]
    send_message(token, chat_id, "\n".join(lines))


def _show_llm_status(token: str, chat_id: int, clients_db: ClientsDb) -> None:
    """Показать статус LLM-расхода по всем клиентам."""
    clients = list_clients(clients_db)
    lines = ["💰 **LLM-расход (лимит $1)**\n"]
    for c in clients:
        uid = c["user_id"]
        spent = clients_db.get_llm_spent(uid)
        can = clients_db.can_use_llm(uid)
        status_icon = "✅" if can else "❌"
        lines.append(f"  {status_icon} `{uid}` — ${spent:.4f}")
    send_message(token, chat_id, "\n".join(lines))


def _reset_llm_cmd(token: str, chat_id: int, args: str, clients_db: ClientsDb) -> None:
    """Сбросить расход LLM для клиента."""
    try:
        user_id = int(args.strip())
    except ValueError:
        send_message(token, chat_id, "❌ Укажи user_id числом.")
        return
    clients_db.reset_llm_spent(user_id)
    send_message(token, chat_id, f"✅ Расход LLM для `{user_id}` обнулён.")


def _register_token_cmd(token: str, chat_id: int, args: str, clients_db: ClientsDb) -> None:
    import os
    parsed = parse_token_input(args)
    if not parsed:
        send_message(token, chat_id, "❌ Неверный формат. Используй: `/register user_id:token`")
        return

    result = register_token(parsed["user_id"], parsed["token"], dict(os.environ))
    if not result["ok"]:
        send_message(token, chat_id, f"❌ {result['error']}")
        return

    # Добавляем клиента в БД
    add_client(clients_db, parsed["user_id"], plan="free", status="active")

    msg = f"✅ Токен зарегистрирован.\nBot ID: {result['info'].get('id')}\nUsername: @{result['info'].get('username')}"
    if result.get("note"):
        msg += f"\nℹ️ {result['note']}"
    send_message(token, chat_id, msg)


def _show_client_info(token: str, chat_id: int, args: str, clients_db: ClientsDb) -> None:
    try:
        user_id = int(args.strip())
    except ValueError:
        send_message(token, chat_id, "❌ Укажи user_id числом.")
        return

    client = get_client(clients_db, user_id)
    if not client:
        send_message(token, chat_id, f"Клиент {user_id} не найден.")
        return

    bindings = get_bindings(clients_db, user_id)
    spent = clients_db.get_llm_spent(user_id)
    lines = [
        f"👤 **Клиент {user_id}**\n",
        f"План: {client.get('plan', 'free')}",
        f"Статус: {client.get('status', 'active')}",
        f"LLM-расход: ${spent:.4f} / $1.00",
        f"Привязок: {len(bindings)}",
        "",
        "Привязки:",
    ]
    for b in bindings:
        lines.append(f"  `{b['chat_id']}` — {b.get('type', '?')} ({b.get('role', '?')})")
    send_message(token, chat_id, "\n".join(lines))
