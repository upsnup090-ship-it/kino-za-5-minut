"""Повторяющиеся сообщения — UI клиента (модуль project_ruch/repeating).

Команды (план free+auto, только владелец бота):

  /repeat                        — экран раздела (список + кнопки)
  /repeat add                    — диалог создания (текст → время → дни)
  /repeat del <id>               — удалить
  /repeat on <id> / off <id>     — вкл/выкл

Диалог создания поглощает сообщения, пока активен (state в ruch_db
post_drafts, kind="repeating"). Текст/медиа по baseline §5.1: текст
поддерживается, медиа и URL-кнопки — нет.

Публикация (тик каждую минуту) — в dispatcher.serve_worker через
``due_now``: сообщения, у которых наступило время в зоне клиента.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pro_code.clients_db import ClientsDb
from pro_code.tg_api import TgError, inline_keyboard, send_message
from project_ruch import repeating as repeating_mod
from project_ruch.config import RuchConfig
from project_ruch.ruch_db import RuchDb

logger = logging.getLogger(__name__)

#: Префикс черновиков повторяющихся сообщений в post_drafts.state.
KIND = "repeating"

DRAFT_STATE = "repeating:await_text"

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

WEEKDAYS_RU = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}

HELP_TEXT = (
    "🔁 **Повторяющиеся сообщения**\n\n"
    "Команды:\n"
    "• `/repeat add` — создать (диалог: текст → время → дни)\n"
    "• `/repeat del <id>` — удалить\n"
    "• `/repeat on <id>` / `/repeat off <id>` — вкл/выкл\n\n"
    "Бот публикует текст в твои каналы с назначением ✍️ ручной постинг\n"
    "(`/channels`). Текст — да, медиа и URL-кнопки — нет."
)


def _ruch_db() -> RuchDb:
    return RuchDb()


def _manual_channels(clients_db: ClientsDb, user_id: int) -> list[dict[str, Any]]:
    return clients_db.list_channels(user_id, purpose="manual")


def _fmt_msg(m: dict[str, Any]) -> str:
    days = m.get("days_week") or "0,1,2,3,4,5,6"
    day_names = {"0": "пн", "1": "вт", "2": "ср", "3": "чт", "4": "пт", "5": "сб", "6": "вс"}
    pretty = ", ".join(day_names.get(d.strip(), d) for d in days.split(",") if d.strip())
    interval = ""
    hours = int(m.get("interval_hours") or 0)
    minutes = int(m.get("interval_minutes") or 0)
    if hours or minutes:
        parts = []
        if hours:
            parts.append(f"{hours}ч")
        if minutes:
            parts.append(f"{minutes}м")
        interval = f", каждые {' '.join(parts)}"
    flags = []
    if m.get("pin"):
        flags.append("📌 закрепление")
    if m.get("delete_last"):
        flags.append("🗑 удалять прошлый")
    flag_str = (" • " + ", ".join(flags)) if flags else ""
    status = "✅" if m.get("enabled") else "⏸"
    dates = ""
    if m.get("start_date") or m.get("end_date"):
        dates = f" (с {m.get('start_date') or '…'} по {m.get('end_date') or '…'})"
    text = (m.get("text") or "")[:50] + ("…" if len(m.get("text") or "") > 50 else "")
    return (
        f"{status} **#{m['id']}** {m['time']} — {pretty}{interval}{dates}{flag_str}\n"
        f"    {text}"
    )


def _send(token: str, chat_id: int, text: str, **kwargs: Any) -> None:
    try:
        send_message(token, chat_id, text, **kwargs)
    except TgError as exc:
        logger.warning("не удалось ответить в чат %s: %s", chat_id, exc)


def show_section(token: str, chat_id: int, user_id: int, ruch_db: RuchDb | None = None) -> None:
    db = ruch_db or _ruch_db()
    close_after = ruch_db is None
    try:
        items = repeating_mod.list_repeating(db, user_id)
    finally:
        if close_after:
            db.close()
    if not items:
        _send(token, chat_id, HELP_TEXT + "\n\nСписок пуст. Создай: `/repeat add`")
        return
    lines = ["🔁 **Повторяющиеся сообщения**\n"]
    lines.extend(_fmt_msg(m) for m in items)
    lines.append("")
    lines.append("Управление: `/repeat del <id>`, `/repeat on/off <id>`, `/repeat add`")
    _send(token, chat_id, "\n".join(lines))


def start_add(token: str, chat_id: int, user_id: int, clients_db: ClientsDb | None = None, ruch_db2: RuchDb | None = None) -> None:
    channels = _manual_channels(clients_db or ClientsDb(), user_id)
    if not channels:
        _send(
            token, chat_id,
            "❌ Нет каналов для публикации. Перешли пост из своего канала сюда\n"
            "или `/add_manual <chat_id>`.",
        )
        return
    db = ruch_db2 or _ruch_db()
    close_after = ruch_db2 is None
    try:
        count = len(repeating_mod.list_repeating(db, user_id))
        limit = RuchConfig().max_repeating_per_client
        if count >= limit:
            _send(token, chat_id, f"❌ Достигнут лимит: {limit} повторяющихся сообщений.")
            return
        db.save_draft(user_id, state=DRAFT_STATE)
    finally:
        if close_after:
            db.close()
    _send(
        token, chat_id,
        "📝 **Шаг 1/3 — текст**\n\n"
        "Отправь текст повторяющегося сообщения.\n"
        "(медиа и URL-кнопки не поддерживаются)",
        reply_markup=inline_keyboard([[("❌ Отмена", "cb:/cancel")]]),
    )


def _on_text(token: str, chat_id: int, user_id: int, text: str, db: RuchDb | None = None) -> None:
    if len(text) > 3500:
        _send(token, chat_id, "❌ Слишком длинный текст (макс. 3500 символов).")
        return
    db = db or _ruch_db()
    db.save_draft(user_id, state="repeating:await_time", text=text)
    _send(
        token, chat_id,
        "⏰ **Шаг 2/3 — время**\n\n"
        "В какое время публиковать? Формат `ЧЧ:ММ` (по Москве).\n"
        "Например: `20:50`",
    )


def _on_time(token: str, chat_id: int, user_id: int, text: str, db: RuchDb | None = None) -> None:
    raw = text.strip()
    if not TIME_RE.match(raw):
        _send(token, chat_id, "❌ Формат времени: `ЧЧ:ММ`, например `20:50`. Попробуй ещё раз.")
        return
    hh, mm = raw.split(":")
    time_norm = f"{int(hh):02d}:{int(mm):02d}"
    db = db or _ruch_db()
    # Поле time в post_drafts отсутствует — возим время в state.
    db.save_draft(user_id, state=f"repeating:await_days:{time_norm}")
    _send(
        token, chat_id,
        "📅 **Шаг 3/3 — дни недели**\n\n"
        "Через запятую: `пн,вт,ср,чт,пт,сб,вс` — все дни.\n"
        "`пн,ср,пт` — только эти.\n"
        "Либо `ежедневно` — все дни.",
    )


def _on_days(token: str, chat_id: int, user_id: int, text: str, db: RuchDb | None = None) -> None:
    raw = text.strip().lower()
    if raw in ("ежедневно", "все", "каждый день", "daily"):
        days_str = "0,1,2,3,4,5,6"
    else:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        days: list[int] = []
        for p in parts:
            if p in WEEKDAYS_RU:
                days.append(WEEKDAYS_RU[p])
            else:
                _send(
                    token, chat_id,
                    "❌ Не понял день. Пиши пн/вт/ср/чт/пт/сб/вс через запятую\n"
                    "или `ежедневно`.",
                )
                return
        days_str = ",".join(str(d) for d in sorted(set(days)))

    db = db or _ruch_db()
    draft = db.get_draft(user_id) or {}
    state = draft.get("state") or ""
    time_norm = state.split(":", 2)[2] if state.count(":") >= 2 else "00:00"
    msg_id = db.add_repeating(
        user_id=user_id,
        text=draft.get("text") or "",
        time=time_norm,
        days_week=days_str,
    )
    db.clear_draft(user_id)
    _send(
        token, chat_id,
        f"✅ **#{msg_id} создано.**\n\n"
        f"Время: `{draft.get('time') or '00:00'}` (МСК), дни: {days_str.replace(',', ', ')}\n"
        f"Каналы публикации — ✍️ ручные (`/channels`).\n"
        f"Выключить: `/repeat off {msg_id}`.",
    )


def handle_add_dialog(
    token: str, chat_id: int, user_id: int, text: str,
    clients_db: ClientsDb, ruch_db: RuchDb,
) -> bool:
    """Активен ли диалог создания. True — сообщение поглощено."""
    draft = ruch_db.get_draft(user_id) or {}
    state = draft.get("state") or ""
    if not state.startswith("repeating:"):
        return False
    if text.strip().lower() in ("/cancel", "отмена", "❌ отмена"):
        ruch_db.clear_draft(user_id)
        _send(token, chat_id, "❌ Отменено.")
        return True
    if state == DRAFT_STATE or state == "repeating:await_text":
        _on_text(token, chat_id, user_id, text, ruch_db)
    elif state == "repeating:await_time":
        _on_time(token, chat_id, user_id, text, ruch_db)
    elif state.startswith("repeating:await_days"):
        _on_days(token, chat_id, user_id, text, ruch_db)
    return True


def handle(
    token: str, chat_id: int, user_id: int, text: str,
    clients_db: ClientsDb, ruch_db: RuchDb,
) -> bool:
    """Команды раздела. True — поглощено."""
    cmd = text.strip().lower()

    if cmd in ("/repeat", "/repeats", "🔁 повторяющиеся", "повторяющиеся", "repeating", "🔁"):
        show_section(token, chat_id, user_id, ruch_db)
        return True

    if not cmd.startswith("/repeat "):
        # Диалог создания активен — поглощаем сообщение.
        if handle_add_dialog(token, chat_id, user_id, text, clients_db, ruch_db):
            return True
        return False

    parts = cmd.split()
    sub = parts[1]

    if sub == "add":
        start_add(token, chat_id, user_id, clients_db, ruch_db)
        return True

    if sub in ("del", "delete", "off", "on"):
        if len(parts) < 3 or not parts[2].isdigit():
            _send(token, chat_id, f"❌ Формат: `/repeat {sub} <id>` (id из `/repeat`)")
            return True
        msg_id = int(parts[2])
        db = ruch_db or _ruch_db()
        close_after = ruch_db is None
        try:
            item = db.get_repeating(msg_id)
            if not item or item["user_id"] != user_id:
                _send(token, chat_id, f"❌ Сообщение #{msg_id} не найдено.")
                return True
            if sub in ("del", "delete"):
                db.delete_repeating(msg_id)
                _send(token, chat_id, f"🗑 #{msg_id} удалено.")
            elif sub == "on":
                if not item["enabled"]:
                    db.toggle_repeating(msg_id)
                _send(token, chat_id, f"▶️ #{msg_id} включено.")
            else:
                if item["enabled"]:
                    db.toggle_repeating(msg_id)
                _send(token, chat_id, f"⏸ #{msg_id} выключено.")
        finally:
            if close_after:
                db.close()
        return True

    if sub == "help":
        _send(token, chat_id, HELP_TEXT)
        return True

    _send(token, chat_id, HELP_TEXT)
    return True


def due_now(
    ruch_db: RuchDb,
    user_id: int,
    token: str,
    clients_db: ClientsDb,
    now: Any = None,
) -> list[dict[str, Any]]:
    """Сообщения, которые нужно опубликовать сейчас.

    Вызывается из serve-цикла диспетчера (тик каждую минуту). Публикует
    во все каналы клиента с назначением ✍️ ручной/both. Возвращает
    опубликованные сообщения.
    """
    tz = RuchConfig().timezone
    if now is not None and now.tzinfo is None:
        # naive-время считаем в зоне клиента (иначе get_due_messages
        # примет его за UTC и слот сдвинется на 3 часа).
        now = now.replace(tzinfo=tz)
    if ruch_db is None:
        ruch_db = RuchDb()
        close_after = True
    else:
        close_after = False
    try:
        due = repeating_mod.get_due_messages(ruch_db, user_id, now=now, tz=tz)
        if not due:
            return []
        channels = _manual_channels(clients_db, user_id)
        published = []
        for msg in due:
            for ch in channels:
                post_id = ruch_db.add_manual_post(
                    user_id=user_id,
                    channel_chat_id=ch["chat_id"],
                    text=msg["text"],
                    media_type="text",
                )
                from project_ruch import manual_post
                result = manual_post.publish_post(ruch_db, post_id, token)
                published.append({
                    "repeat_id": msg["id"],
                    "post_id": post_id,
                    "chat_id": ch["chat_id"],
                    "ok": result.get("ok", False),
                })
            logger.info(
                "repeating: опубликовано %d каналов для user_id=%d (сообщение #%d)",
                len(channels), user_id, msg["id"],
            )
        return published
    finally:
        if close_after:
            ruch_db.close()
