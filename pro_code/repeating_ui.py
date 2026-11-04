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
import json
import re
from datetime import date, datetime, timezone
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
    # Не затирать активный диалог ручного постинга.
    db_probe = ruch_db2
    if db_probe is None:
        db_probe = _ruch_db()
        current = (db_probe.get_draft(user_id) or {}).get("state") or ""
        db_probe.close()
    else:
        current = (db_probe.get_draft(user_id) or {}).get("state") or ""
    if current in ("await_channel", "await_content", "await_confirm"):
        _send(token, chat_id,
              "⏳ Сначала заверши создание поста (отправь его или `/cancel`).")
        return
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
    extra = _load_extra(db, user_id)
    extra["text_len"] = len(text)
    db.save_draft(user_id, state="repeating:await_time", text=text, extra=json_dumps(extra))
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
    extra = _load_extra(db, user_id)
    extra["time"] = time_norm
    db.save_draft(user_id, state="repeating:await_days", extra=json_dumps(extra))
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
    extra = _load_extra(db, user_id)
    extra["days_week"] = days_str
    db.save_draft(user_id, state="repeating:await_opts", extra=json_dumps(extra))
    _send(
        token, chat_id,
        "⚙️ **Шаг 4/4 — опции** (можно пропустить: отправь `—`)\n\n"
        "Формат: `интервал 24, пин, удалять, с 01.11 по 30.11, дни месяца 1,15`\n"
        "• `интервал N` — повторять каждые N часов\n"
        "• `пин` — закреплять опубликованное\n"
        "• `удалять` — удалять предыдущий пост\n"
        "• `с ДД.ММ по ДД.ММ` — период действия\n"
        "• `дни месяца 1,15` — только эти числа",
    )


def _load_extra(db: RuchDb, user_id: int) -> dict:
    draft = db.get_draft(user_id) or {}
    raw = draft.get("extra")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def json_dumps(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _parse_options(raw: str) -> dict | None:
    """Парсер строки опций шага 4. None — есть нераспознанный токен."""
    opts = {
        "interval_hours": 24, "interval_minutes": 0,
        "pin": False, "delete_last": False,
        "start_date": None, "end_date": None, "days_month": "",
    }
    raw = raw.strip().lower()
    if raw in ("—", "-", "нет", "no", "skip", ""):
        return opts
    DATE_RE = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?$")

    def parse_date_tok(tok: str) -> str | None:
        m = DATE_RE.match(tok)
        if not m:
            return None
        d, mo, y = m.groups()
        year = int(y) if y else 2026  # без года — текущий
        if len(str(y or "")) == 2:
            year = 2000 + int(y)
        try:
            return date(year, int(mo), int(d)).isoformat()
        except ValueError:
            return None

    # «с X по Y» и «дни месяца 1,15» содержат запятые внутри — нормализуем:
    raw = re.sub(r"\bс\s+(\S+)\s+по\s+(\S+)", r"с \1 по \2", raw)
    raw = re.sub(r"(дни месяца[^,]+),\s*(\d+)", r"\1;\2", raw)
    for tok in [t.strip().replace(";", ",") for t in raw.split(",")]:
        if tok in ("пин", "pin"):
            opts["pin"] = True
        elif tok in ("удалять", "удалить", "del_last", "удаление"):
            opts["delete_last"] = True
        elif tok.startswith("интервал"):
            num = re.sub(r"[^0-9]", "", tok)
            if not num:
                return None
            opts["interval_hours"] = int(num)
            opts["interval_minutes"] = 0
        elif " по " in tok and tok.startswith("с "):
            # сегмент «с ДД.ММ по ДД.ММ» (даты могут быть в одном токене)
            left, right = tok[2:].split(" по ", 1)
            d1, d2 = parse_date_tok(left.strip()), parse_date_tok(right.strip())
            if not d1 or not d2:
                return None
            opts["start_date"], opts["end_date"] = d1, d2
        elif tok.startswith("с "):
            d = parse_date_tok(tok[2:])
            if not d:
                return None
            opts["start_date"] = d
        elif tok.startswith("по "):
            d = parse_date_tok(tok[3:])
            if not d:
                return None
            opts["end_date"] = d
        elif tok.startswith("дни месяца"):
            nums = re.sub(r"[^0-9, ]", "", tok[len("дни месяца"):])
            nums = [n.strip() for n in nums.split(",") if n.strip()]
            if not nums or any(not n.isdigit() or not (1 <= int(n) <= 31) for n in nums):
                return None
            opts["days_month"] = ",".join(nums)
        else:
            return None
    return opts


def _on_opts(token: str, chat_id: int, user_id: int, text: str, db: RuchDb | None = None) -> None:
    opts = _parse_options(text)
    if opts is None:
        _send(
            token, chat_id,
            "❌ Не понял опцию. Формат: `интервал 24, пин, удалять, с 01.11 по 30.11, дни месяца 1,15`\n"
            "Или отправь `—` чтобы пропустить.",
        )
        return

    db = db or _ruch_db()
    draft = db.get_draft(user_id) or {}
    extra = _load_extra(db, user_id)
    msg_id = db.add_repeating(
        user_id=user_id,
        text=draft.get("text") or "",
        time=extra.get("time") or "00:00",
        days_week=extra.get("days_week") or "0,1,2,3,4,5,6",
        interval_hours=opts["interval_hours"],
        interval_minutes=opts["interval_minutes"],
        days_month=opts["days_month"],
        start_date=opts["start_date"],
        end_date=opts["end_date"],
        pin=opts["pin"],
        delete_last=opts["delete_last"],
    )
    db.clear_draft(user_id)
    parts = [f"✅ **#{msg_id} создано.**\n"]
    parts.append(f"Время: `{extra.get('time') or '00:00'}` (МСК)")
    days = extra.get("days_week") or "0,1,2,3,4,5,6"
    parts.append(f"Дни: {days.replace(',', ', ')}")
    if opts["interval_hours"] != 24:
        parts.append(f"Интервал: каждые {opts['interval_hours']} ч")
    if opts["pin"]:
        parts.append("📌 закрепление")
    if opts["delete_last"]:
        parts.append("🗑 удаление предыдущего")
    if opts["start_date"] or opts["end_date"]:
        parts.append(f"Период: {opts['start_date'] or '…'} — {opts['end_date'] or '…'}")
    if opts["days_month"]:
        parts.append(f"Дни месяца: {opts['days_month']}")
    parts.append("")
    parts.append("Каналы публикации — ✍️ ручные (`/channels`).")
    parts.append(f"Выключить: `/repeat off {msg_id}`.")
    _send(token, chat_id, "\n".join(parts))


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
    elif state == "repeating:await_days":
        _on_days(token, chat_id, user_id, text, ruch_db)
    elif state == "repeating:await_opts":
        _on_opts(token, chat_id, user_id, text, ruch_db)
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

    Дедуп: сообщение публикуется один раз в сутки — отметка в
    ``repeating_sent`` (user_id, repeat_id, sent_date в зоне клиента).
    Поэтому окно «время наступило» (``time <= now``), а не равенство
    минуты: пропущенный тик (занятый serve, рестарт) не теряет пост —
    он уйдёт на первом тике после времени.
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
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        local = moment.astimezone(tz)
        now_time = local.strftime("%H:%M")
        today = local.strftime("%Y-%m-%d")

        due = [
            msg for msg in ruch_db.list_repeating(user_id)
            if msg.get("enabled")
            and _time_le(str(msg.get("time") or ""), now_time)
            and not ruch_db.repeating_sent_today(user_id, msg["id"], today)
        ]
        # Даты начала/окончания и дни — повторно проверяем через
        # get_due_messages-логику: фильтруем только по дате/дням.
        due = _filter_by_dates_and_days(due, local)
        if not due:
            return []

        channels = _manual_channels(clients_db, user_id)
        if not channels:
            return []

        from pro_code.tg_api import delete_message as tg_delete, pin_chat_message

        published = []
        for msg in due:
            last_map: dict[str, int] = {}
            if msg.get("last_message_id"):
                try:
                    last_map = json.loads(msg["last_message_id"])
                except (ValueError, TypeError):
                    last_map = {}
            sent_map: dict[str, int] = {}
            for ch in channels:
                ch_id = str(ch["chat_id"])
                # delete_last: убрать предыдущий пост в этом канале
                if msg.get("delete_last") and ch_id in last_map:
                    tg_delete(token, ch["chat_id"], last_map[ch_id])
                post_id = ruch_db.add_manual_post(
                    user_id=user_id,
                    channel_chat_id=ch["chat_id"],
                    text=msg["text"],
                    media_type="text",
                )
                from project_ruch import manual_post
                result = manual_post.publish_post(ruch_db, post_id, token)
                ok = result.get("ok", False)
                message_id = result.get("message_id")
                if ok and message_id:
                    sent_map[ch_id] = int(message_id)
                    if msg.get("pin"):
                        pin_chat_message(token, ch["chat_id"], int(message_id))
                ruch_db.add_repeating_sent(
                    user_id, msg["id"], today, message_id
                )
                published.append({
                    "repeat_id": msg["id"],
                    "post_id": post_id,
                    "chat_id": ch["chat_id"],
                    "ok": ok,
                })
            if sent_map:
                ruch_db.set_repeating_last_messages(msg["id"], sent_map)
            logger.info(
                "repeating: опубликовано %d каналов для user_id=%d (сообщение #%d)",
                len(channels), user_id, msg["id"],
            )
        return published
    finally:
        if close_after:
            ruch_db.close()


def _time_le(msg_time: str, now_time: str) -> bool:
    """msg_time <= now_time (строки HH:MM; None/битые → False)."""
    try:
        th, tm = msg_time.split(":")[:2]
        nh, nm = now_time.split(":")[:2]
        return (int(th), int(tm)) <= (int(nh), int(nm))
    except (ValueError, AttributeError):
        return False


def _filter_by_dates_and_days(msgs: list[dict[str, Any]], local: Any) -> list[dict[str, Any]]:
    """Фильтр по start/end датам, дням недели и дням месяца (локальная дата)."""
    weekday = str(local.weekday())
    day = str(local.day)
    today = local.date()
    result = []
    for msg in msgs:
        start = repeating_mod._parse_date(msg.get("start_date"))
        if start and today < start:
            continue
        end = repeating_mod._parse_date(msg.get("end_date"))
        if end and today > end:
            continue
        days_week = (msg.get("days_week") or "").split(",")
        if days_week and days_week != [""] and weekday not in [d.strip() for d in days_week]:
            continue
        days_month = (msg.get("days_month") or "").split(",")
        if days_month and days_month != [""] and day not in [d.strip() for d in days_month]:
            continue
        result.append(msg)
    return result
