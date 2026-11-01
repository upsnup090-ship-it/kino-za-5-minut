"""Обработка нажатий: `callback_data` → экран.

Схема колбэков — из спецификации §7.4 (`nav:*`, `slot:<id>`, `state:pause`,
`post:<mode>`). Старые имена (`home`, `schedule`, `runs`) остаются рабочими:
у владельца в чате висят сообщения со старыми кнопками.
"""

from __future__ import annotations

from datetime import datetime

from kino.config import Config
from kino.schedule_db import ScheduleDb

from kino.admin.common import Reply, _btn
from kino.admin.commands import _err, _handle_slot
from kino.admin.navigation import _handle_nav, _handle_state
from kino.admin.posting import handle_post_button
from kino.admin.screens import (
    screen_home,
    screen_preview,
    screen_runs,
    screen_schedule,
    screen_slot,
)

# --- кнопки ------------------------------------------------------------------


def handle_callback(cfg: Config, data: str, now: datetime | None = None) -> Reply:
    """Обрабатывает нажатие кнопки. `data` — callback_data.

    Схема колбэков — из спецификации §7.4: `nav:<экран>`, `slot:<id>`,
    `state:pause`, `post:<mode>`. Старые имена (`home`, `schedule`, `runs`)
    остаются рабочими: у владельца в чате висят сообщения со старыми
    кнопками, и «кнопка устарела» на них — плохой ответ.
    """
    db = ScheduleDb(cfg.db_path)
    action, _, arg = (data or "").partition(":")

    if action == "home":
        return screen_home(cfg)
    if action == "schedule":
        return screen_schedule(cfg, now)
    if action == "post":
        return handle_post_button(cfg, arg, now)
    if action == "seed":
        db.seed_defaults()
        reply = screen_schedule(cfg, now)
        reply.text = f"Сетка заполнена: слотов {db.count_slots()}.\n\n{reply.text}"
        return reply
    if action == "preview":
        return screen_preview(cfg, now)
    if action == "runs":
        return screen_runs(cfg)

    if action == "nav":
        return _handle_nav(cfg, arg, now)
    if action == "state":
        return _handle_state(cfg, arg, now)

    if action == "add":
        return Reply(
            text=(
                "Добавление слота.\n"
                "Напиши день и время одной строкой: пт 23:40\n"
                "Или командой: /slot add пт 23:40"
            ),
            keyboard=[[_btn("◀️ Назад", "schedule")]],
        )
    if action in ("on", "off", "del", "edit", "slot"):
        try:
            slot_id = int(arg)
        except ValueError:
            return screen_schedule(cfg, now)
        if action == "slot":
            return screen_slot(cfg, slot_id)
        slot = next((s for s in db.list_slots() if s.id == slot_id), None)
        if slot is None:
            return screen_schedule(cfg, now)

        if action == "edit":
            return Reply(
                text=(
                    f"Новое время для {slot.weekday_name} (было {slot.time}).\n"
                    f"Напиши HH:MM, например 21:40.\n"
                    f"Или командой: /slot add {slot.weekday_name} 21:40"
                ),
                keyboard=[[_btn("◀️ Назад", f"slot:{slot_id}")]],
            )
        if action == "del":
            db.delete_slot(slot.weekday, slot.time)
            reply = screen_schedule(cfg, now)
            reply.text = f"Слот {slot.weekday_name} {slot.time} удалён.\n\n{reply.text}"
            return reply
        # on / off
        db.set_enabled(slot.weekday, slot.time, action == "on")
        return screen_slot(cfg, slot_id)

    return screen_schedule(cfg, now)

