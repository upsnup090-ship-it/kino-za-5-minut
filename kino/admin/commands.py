"""Текстовые команды: `/start`, `/schedule`, `/slot`, `/next`.

Команда отвечает тем же экраном с кнопками, что и нажатие: два пути к
одному состоянию, а не два разных интерфейса.
"""

from __future__ import annotations

from datetime import datetime

from kino.config import Config
from kino.schedule_db import WEEKDAY_NAMES, ScheduleDb, ScheduleDbError

from kino.admin.common import Reply, _btn
from kino.admin.screens import screen_home, screen_preview, screen_schedule
from kino.admin.validation import (
    check_add,
    day_count,
    validate_time,
    validate_weekday,
    warn_slots_per_day,
)

# --- команды -----------------------------------------------------------------


def handle_command(cfg: Config, text: str, now: datetime | None = None) -> Reply:
    """Разбирает команду и возвращает экран. Неизвестная — подсказку."""
    parts = (text or "").split()
    if not parts:
        return _help()
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]

    if cmd in ("/start", "/admin"):
        return screen_home(cfg)
    if cmd == "/help":
        return _help()
    if cmd == "/schedule":
        return screen_schedule(cfg, now)
    if cmd == "/next":
        return screen_preview(cfg, now)
    if cmd == "/slot":
        return _handle_slot(cfg, args, now)
    return _help()


def _help() -> Reply:
    """Короткая справка. Постинг и расписание живут на кнопках главного экрана.

    Каждая команда, которую принимает `handle_command`, названа здесь: справка,
    которая молчит про работающую команду, — это баг, а не стиль. Тест
    `test_command_unknown_shows_help` это и держит.
    """
    return Reply(
        text=(
            "Всё на кнопках:\n"
            "/start или /admin — главный экран\n"
            "🧪 тестовый пост — в личку\n"
            "🚀 финальный пост — в канал\n"
            "📅 публикации — сетка, слоты, прогоны\n"
            "\nКоманды:\n"
            "/schedule — расписание (тот же экран, что кнопка 📅)\n"
            "/next — ближайшие 7 дней\n"
            "/slot add|del|on|off <день> <HH:MM>\n"
            "/slot clear <день>"
        ),
        keyboard=[[_btn("◀️ Главный экран", "home")]],
    )


def _handle_slot(cfg: Config, args: list[str], now: datetime | None) -> Reply:
    if not args:
        return Reply(
            text="Нужно действие: add, del, on, off, clear.",
            keyboard=[[_btn("📅 Расписание", "schedule")]],
        )
    action = args[0].lower()
    db = ScheduleDb(cfg.db_path)

    if action == "clear":
        if len(args) < 2:
            return _err("Нужен день: /slot clear пт")
        day = validate_weekday(args[1])
        if not day.ok:
            return _err(day.error)
        removed = db.clear_weekday(int(day.value))
        reply = screen_schedule(cfg, now)
        reply.text = f"Удалено слотов: {removed}\n\n{reply.text}"
        return reply

    if action not in ("add", "del", "on", "off"):
        return _err(f"Неизвестное действие «{action}». Есть: add, del, on, off, clear.")
    if len(args) < 3:
        return _err(f"Нужно: /slot {action} <день> <HH:MM>")

    day = validate_weekday(args[1])
    if not day.ok:
        return _err(day.error)
    stamp = validate_time(args[2])
    if not stamp.ok:
        return _err(stamp.error)
    weekday = int(day.value)

    try:
        if action == "add":
            check = check_add(cfg, weekday, stamp.value)
            if not check.ok:
                return _err(check.error)
            db.add_slot(weekday, stamp.value)
            reply = screen_schedule(cfg, now)
            warn = warn_slots_per_day(day_count(cfg, weekday))
            reply.text = f"Добавлен слот {WEEKDAY_NAMES[weekday]} {stamp.value}.{warn}\n\n{reply.text}"
            return reply

        if action == "del":
            if not db.delete_slot(weekday, stamp.value):
                return _err(f"Слота {WEEKDAY_NAMES[weekday]} {stamp.value} нет.")
            reply = screen_schedule(cfg, now)
            reply.text = f"Слот {WEEKDAY_NAMES[weekday]} {stamp.value} удалён.\n\n{reply.text}"
            return reply

        # on / off
        slot = db.set_enabled(weekday, stamp.value, action == "on")
        state = "включён" if slot.enabled else "выключен"
        reply = screen_schedule(cfg, now)
        reply.text = f"Слот {WEEKDAY_NAMES[weekday]} {stamp.value} {state}.\n\n{reply.text}"
        return reply
    except ScheduleDbError as exc:
        return _err(str(exc))


def _err(message: str) -> Reply:
    return Reply(text=f"❌ {message}", keyboard=[[_btn("📅 Расписание", "schedule")]])

