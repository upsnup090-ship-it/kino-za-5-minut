"""Управление слотами автопостинга из бота клиента.

Команды (план auto, только владелец бота):

  /slots               — список слотов
  /slot add <день> <HH:MM>  — добавить (день: пн…вс или 0–6)
  /slot del <id>       — удалить
  /slot on <id> / /slot off <id> — включить/выключить

Слоты лежат в ``kino_auto.db`` (schedule_slots, per-client) — их читает
serve-цикл диспетчера. Время — в зоне клиента (RuchConfig().timezone).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pro_code.clients_db import ClientsDb
from pro_code.kino_auto_db import KinoAutoDb
from pro_code.tg_api import send_message

logger = logging.getLogger(__name__)

WEEKDAYS = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}
WEEKDAY_NAMES = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

HELP_TEXT = (
    "🤖 **Мои слоты автопостинга**\n\n"
    "Команды:\n"
    "• `/slots` — список\n"
    "• `/slot add пн 20:50` — добавить\n"
    "• `/slot del 3` — удалить по номеру\n"
    "• `/slot off 3` / `/slot on 3` — выключить/включить\n\n"
    "Время — по Москве. В слот бот публикует пост во все\n"
    "твои каналы с назначением 🤖 автопостинг (`/channels`)."
)


def handle_slots_command(
    token: str,
    chat_id: int,
    user_id: int,
    text: str,
    clients_db: ClientsDb,
    auto_db: KinoAutoDb,
    plan: str,
) -> bool:
    """Обработать slot-команду. True — сообщение поглощено."""
    cmd = text.strip().lower()

    if cmd in ("/slots", "мои слоты", "🗓 мои слоты"):
        if plan != "auto":
            send_message(token, chat_id, "❌ Автопостинг доступен только по плану **auto**.")
            return True
        _show_slots(token, chat_id, user_id, auto_db)
        return True

    if not cmd.startswith("/slot "):
        return False

    if plan != "auto":
        send_message(token, chat_id, "❌ Автопостинг доступен только по плану **auto**.")
        return True

    parts = cmd.split()
    sub = parts[1] if len(parts) > 1 else ""

    if sub == "help" or sub == "":
        send_message(token, chat_id, HELP_TEXT)
        return True

    if sub == "add":
        if len(parts) < 4:
            send_message(token, chat_id, "❌ Формат: `/slot add <пн…вс> <HH:MM>`")
            return True
        day = parts[2]
        weekday = WEEKDAYS.get(day)
        if weekday is None:
            try:
                weekday = int(day)
                if not 0 <= weekday <= 6:
                    raise ValueError
            except ValueError:
                send_message(token, chat_id,
                    "❌ День: пн, вт, ср, чт, пт, сб, вс (или 0–6).")
                return True
        t = parts[3]
        if not TIME_RE.match(t):
            send_message(token, chat_id, "❌ Время в формате HH:MM, например 20:50.")
            return True
        t = f"{int(t.split(':')[0]):02d}:{t.split(':')[1]}"
        slot = auto_db.add_slot(user_id, weekday, t)
        send_message(token, chat_id,
            f"✅ Слот добавлен: **{WEEKDAY_NAMES[weekday]} {t}** (id {slot['id']}).")
        return True

    if sub in ("del", "delete"):
        if len(parts) < 3 or not parts[2].isdigit():
            send_message(token, chat_id, "❌ Формат: `/slot del <id>` (id из `/slots`).")
            return True
        ok = auto_db.delete_slot(int(parts[2]), user_id=user_id)
        send_message(token, chat_id,
            "✅ Слот удалён." if ok else "❌ Слот не найден (см. `/slots`).")
        return True

    if sub in ("on", "off"):
        if len(parts) < 3 or not parts[2].isdigit():
            send_message(token, chat_id, f"❌ Формат: `/slot {sub} <id>`.")
            return True
        ok = auto_db.set_slot_enabled(int(parts[2]), sub == "on", user_id=user_id)
        send_message(token, chat_id,
            (f"✅ Слот {sub == 'on' and 'включён' or 'выключен'}." if ok
             else "❌ Слот не найден (см. `/slots`)."))
        return True

    send_message(token, chat_id, HELP_TEXT)
    return True


def _show_slots(token: str, chat_id: int, user_id: int, auto_db: KinoAutoDb) -> None:
    slots = auto_db.list_slots(user_id)
    if not slots:
        send_message(token, chat_id,
            "🗓 Слотов нет.\n\n" + HELP_TEXT)
        return
    lines = ["🗓 **Мои слоты автопостинга**\n"]
    for s in slots:
        flag = "✅" if s["enabled"] else "⏸"
        lines.append(f"{flag} id {s['id']} — {WEEKDAY_NAMES[s['weekday']]} {s['time']}")
    lines.append("\nИзменение: `/slot add|del|on|off <id>`")
    send_message(token, chat_id, "\n".join(lines))
