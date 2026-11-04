"""Маршрутизация команд owner (RootA) из чата мастера.

Подключается к pro_code/router.py: если апдейт от admin_chat_id,
команды уходят сюда вместо обычного меню.
"""

from __future__ import annotations

import logging
from typing import Any

from pro_code.clients_db import ClientsDb
from pro_code.tg_api import send_message
import pro_code.activation as activation
from project_ruch.ruch_db import RuchDb
from rrA.backup_logs import list_backups, make_backup, export_logs
from rrA.clients import list_clients, get_client, add_client, set_plan, set_status, get_bindings, verify_client_token
from rrA.stats import full_owner_report
from rrA.tokens import parse_token_input, register_token

logger = logging.getLogger(__name__)

#: Команды owner — единый источник истины; router.py берёт отсюда.
OWNER_COMMANDS = (
    "/clients", "/stats", "/backup", "/backups", "/logs",
    "/register", "/client", "/llm_status", "/reset_llm", "/set_llm_limit",
    "/set_plan", "/set_status",
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

    # Смена плана и статуса
    if cmd.startswith("/set_plan "):
        _set_plan_cmd(token, chat_id, text[10:], clients_db)
        return
    if cmd.startswith("/set_status "):
        _set_status_cmd(token, chat_id, text[12:], clients_db)
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
        "🔁 /set_plan user_id free|auto — сменить план\n"
        "⏸ /set_status user_id active|inactive — вкл/выкл клиента\n"
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
        bot = clients_db.get_bot_info(c["user_id"])
        bot_part = f"@{bot['bot_username']}" if bot and bot.get("bot_username") else "бота нет"
        active_part = "✅" if (bot and bot.get("active")) else "⏸"
        added = (bot.get("added_at") or "")[:10] if bot else "—"
        status_icon = "🟢" if c.get("status") == "active" else "⚪️"
        lines.append(
            f"{status_icon} `{c['user_id']}` {active_part} {bot_part} | "
            f"{c.get('plan', 'free')} | ${spent:.4f} | с {added}"
        )
    send_message(token, chat_id, "\n".join(lines))


def _set_plan_cmd(token: str, chat_id: int, args: str, clients_db: ClientsDb) -> None:
    """/set_plan <user_id> <free|auto>."""
    parts = args.strip().split()
    if len(parts) != 2 or not parts[0].isdigit():
        send_message(token, chat_id, "❌ Формат: `/set_plan user_id free|auto`")
        return
    user_id = int(parts[0])
    plan = parts[1].lower()
    if not set_plan(clients_db, user_id, plan):
        send_message(token, chat_id, "❌ План: `free` или `auto`.")
        return
    note = (
        "Serve-цикл подхватит план в течение минуты.\n"
        "⚠️ Если бот клиента ещё не запущен (например, план менялся с free на "
        "auto до активации) — вступит после активации или рестарта диспетчера."
        if plan == "auto" else
        "Автопостинг клиента остановится в течение минуты."
    )
    send_message(token, chat_id, f"✅ План `{user_id}` → **{plan}**.\n{note}")


def _set_status_cmd(token: str, chat_id: int, args: str, clients_db: ClientsDb) -> None:
    """/set_status <user_id> <active|inactive>."""
    parts = args.strip().split()
    if len(parts) != 2 or not parts[0].isdigit():
        send_message(token, chat_id, "❌ Формат: `/set_status user_id active|inactive`")
        return
    user_id = int(parts[0])
    status = parts[1].lower()
    if not set_status(clients_db, user_id, status):
        send_message(token, chat_id, "❌ Статус: `active` или `inactive`.")
        return
    note = (
        "Бот клиента будет пропущен при следующем рестарте диспетчера "
        "(живой polling-поток в этой версии не останавливается)."
        if status == "inactive" else
        "Бот клиента будет поднят при следующем рестарте диспетчера."
    )
    send_message(token, chat_id, f"✅ Статус `{user_id}` → **{status}**.\n{note}")


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
    """Хвост реального лога ручного модуля + подсказка по journald."""
    from pathlib import Path

    logs_dir = Path(__file__).resolve().parents[1] / "project_ruch" / "logs"
    files = sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    lines = ["📜 **Логи**\n"]
    if files:
        f = files[0]
        tail = f.read_text(errors="replace").splitlines()[-20:]
        lines.append(f"Файл: `{f.name}` (последние 20 строк):\n")
        lines.extend(f"`{ln}`" if ln.strip() else "" for ln in tail)
        lines.append("")
    else:
        lines.append("`project_ruch/logs/*.log` — файлов нет.\n")
    lines.append("Журнал прогона на сервере:\n`journalctl -u kino-dispatcher -n 100 --no-pager`")
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
    parsed = parse_token_input(args)
    if not parsed:
        send_message(token, chat_id, "❌ Неверный формат. Используй: `/register user_id:token`")
        return

    result = register_token(parsed["user_id"], parsed["token"], dict(os.environ))
    if not result["ok"]:
        send_message(token, chat_id, f"❌ {result['error']}")
        return

    bot_info = result["info"]

    # Сохраняем токен в БД (шифрование внутри) — раньше токен только
    # «регистрировался» и терялся: бот не работал без правки .env.
    spawned = False
    try:
        existing = clients_db.get_client(parsed["user_id"])
        plan = (existing or {}).get("plan") if existing else "free"
        if plan not in ("free", "auto"):
            plan = "free"
        clients_db.upsert_client(parsed["user_id"], plan=plan, status="active")
        clients_db.set_bot_token(
            parsed["user_id"], parsed["token"],
            bot_id=bot_info.get("id"), bot_username=bot_info.get("username"),
        )
        # Спавн бота без рестарта: колбэк диспетчера, зарегистрированный
        # через activation.set_spawner при старте.
        if activation._spawn is not None:
            try:
                activation._spawn(parsed["user_id"], parsed["token"])
                spawned = True
            except Exception as exc:
                logger.exception("spawn после /register: %s", exc)
    except Exception as exc:
        logger.exception("сохранение токена после /register: %s", exc)
        send_message(token, chat_id, "❌ Токен не сохранён (ошибка БД/шифрования). См. лог.")
        return

    msg = (
        f"✅ Токен зарегистрирован.\n"
        f"Bot ID: {bot_info.get('id')}\n"
        f"Username: @{bot_info.get('username')}\n"
        f"План: {plan}\n"
    )
    if spawned:
        msg += "🚀 Бот запущен — polling активен."
    else:
        msg += "⏳ Бот запустится при следующем рестарте диспетчера."
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
