"""Админка расписания в личке бота.

Две поверхности, обе обязательны (шаг 10):

- **кнопки** — основной UI: экран «Расписание публикаций», карточка слота,
  добавление/правка времени текстом, превью ближайших 7 дней;
- **команды** — быстрый путь: `/schedule`, `/slot add|del|on|off|clear`,
  `/next`. Команда отвечает тем же экраном с кнопками, а не отдельным
  текстом: два пути к одному состоянию.

Доступ только по `ADMIN_CHAT_ID`, проверка на каждом апдейте; чужой чат —
отказ и запись в лог.

Модуль разделён на две части:

- **чистая логика** (`screen_*`, `validate_*`, `handle_command`,
  `handle_callback`) — принимает состояние, возвращает `Reply` (текст +
  клавиатура). Без сети, без Telegram: это то, что проверяют тесты.
- **транспорт** (`poll_once`, `run`) — забирает апдейты через `kino.tg_api`,
  отдаёт их чистой логике, отправляет ответ.

Превью «ближайшие 7 дней» считается теми же функциями, что и планировщик
(`due_slot`/`next_occurrence` из `kino.schedule`), а не отдельной копией
логики: иначе экран и цикл разойдутся.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from kino.config import Config
from kino.schedule import SlotConfig, next_occurrence
from kino.schedule_db import (
    WEEKDAY_NAMES,
    ScheduleDb,
    ScheduleDbError,
    format_week,
    normalize_time,
    normalize_weekday,
)

logger = logging.getLogger(__name__)

#: Больше 10 слотов в день — предупреждение (не запрет): сетка владельца
#: нигде не превышает 3, но правило должно ловить опечатку в руке.
SLOTS_PER_DAY_WARN = 10

#: Сколько записей журнала показывать на экране прогонов.
RUNS_SCREEN_LIMIT = 20


@dataclass
class Reply:
    """Ответ админке: текст и инлайн-клавиатура. Чистые данные, без сети."""

    text: str
    keyboard: list[list[dict[str, str]]] = field(default_factory=list)

    def markup(self) -> dict[str, Any] | None:
        return {"inline_keyboard": self.keyboard} if self.keyboard else None


def _btn(text: str, data: str) -> dict[str, str]:
    return {"text": text, "callback_data": data}


# --- экраны ------------------------------------------------------------------


def screen_schedule(cfg: Config, now: datetime | None = None) -> Reply:
    """Главный экран: сетка по дням, каждый слот — кнопка."""
    db = ScheduleDb(cfg.db_path)
    slots = db.list_slots()
    if not slots:
        return Reply(
            text="📅 Расписание пусто.\nЗаполнить сеткой владельца (13 слотов)?",
            keyboard=[[_btn("🌱 Заполнить сетку", "seed")]],
        )

    lines = ["📅 Расписание публикаций", f"слотов: {len(slots)} "
             f"(включено {sum(1 for s in slots if s.enabled)})", ""]
    keyboard: list[list[dict[str, str]]] = []
    for day in range(7):
        day_slots = [s for s in slots if s.weekday == day]
        stamps = ", ".join(
            f"{s.time}{'' if s.enabled else ' ⏸'}" for s in day_slots
        ) or "—"
        lines.append(f"{WEEKDAY_NAMES[day]}: {stamps}")
        row = [
            _btn(f"{WEEKDAY_NAMES[day]} {s.time}{'' if s.enabled else '⏸'}", f"slot:{s.id}")
            for s in day_slots
        ]
        if row:
            keyboard.append(row)

    keyboard.append([_btn("➕ Добавить слот", "add")])
    keyboard.append([
        _btn("👁 Ближайшие 7 дней", "preview"),
        _btn("📜 Прогоны", "runs"),
    ])
    return Reply(text="\n".join(lines), keyboard=keyboard)


def screen_slot(cfg: Config, slot_id: int) -> Reply:
    """Карточка слота: последний прогон и действия."""
    db = ScheduleDb(cfg.db_path)
    slot = next((s for s in db.list_slots() if s.id == slot_id), None)
    if slot is None:
        return screen_schedule(cfg)

    last = ""
    try:
        runs = [r for r in db.recent_runs(limit=50)
                if r.slot_id == slot_id and r.finished_at]
    except ScheduleDbError:
        runs = []
    if runs:
        rec = runs[0]
        stamp = rec.slot_at.replace("T", " ")
        tail = f" — {rec.reason}" if rec.reason else ""
        last = f"\nПоследний прогон: {stamp} → {rec.outcome}{tail}"
    else:
        last = "\nПрогонов ещё не было"

    text = (
        f"🕐 Слот {slot.weekday_name} {slot.time}\n"
        f"Состояние: {'включён' if slot.enabled else 'выключен'}{last}"
    )
    toggle = "⏸ Выключить" if slot.enabled else "▶️ Включить"
    toggle_data = f"off:{slot.id}" if slot.enabled else f"on:{slot.id}"
    return Reply(
        text=text,
        keyboard=[
            [_btn(toggle, toggle_data), _btn("✏️ Изменить время", f"edit:{slot_id}")],
            [_btn("🗑 Удалить", f"del:{slot_id}")],
            [_btn("◀️ Назад", "schedule")],
        ],
    )


def screen_preview(cfg: Config, now: datetime | None = None) -> Reply:
    """Ближайшие 7 дней — теми же функциями, что и планировщик."""
    db = ScheduleDb(cfg.db_path)
    slots = [(s.id, s.weekday, s.time) for s in db.list_slots(enabled_only=True)]
    if not slots:
        return Reply(text="Слотов нет — публиковать нечего.", keyboard=[[_btn("◀️ Назад", "schedule")]])

    slot_cfg = SlotConfig(
        catchup_minutes=cfg.settings.schedule_catchup_minutes,
        late_grace_minutes=cfg.settings.schedule_late_grace_minutes,
        tz=cfg.settings.timezone,
    )
    moment = slot_cfg.local_now(now)
    lines = ["👁 Ближайшие 7 дней", ""]
    horizon = moment + timedelta(days=7)
    upcoming_all = _upcoming_list(slots, slot_cfg, moment, horizon)
    for upcoming in upcoming_all:
        lines.append(
            f"{upcoming.when.strftime('%d.%m')} "
            f"{WEEKDAY_NAMES[upcoming.weekday]}  {upcoming.time}"
        )
    if not upcoming_all:
        lines.append("в ближайшие 7 дней слотов нет")
    else:
        lines.append("")
        lines.append(f"итого: {len(upcoming_all)}")
    return Reply(text="\n".join(lines), keyboard=[[_btn("◀️ Назад", "schedule")]])


def _upcoming_list(
    slots: list[tuple[int, int, str]],
    slot_cfg: SlotConfig,
    moment: datetime,
    horizon: datetime,
) -> list[Any]:
    """Все слоты в (moment, horizon] — тем же `next_occurrence`, что у цикла.

    Отдельной копии логики нет намеренно: превью и планировщик должны
    считать одинаково, иначе экран обещает не то, что произойдёт.
    """
    found: list[Any] = []
    cursor = moment
    while len(found) <= 50:  # защита от зацикливания на битой сетке
        upcoming = next_occurrence(cursor, slots, slot_cfg)
        if upcoming is None or upcoming.when > horizon:
            break
        found.append(upcoming)
        # Сдвиг на минуту: next_occurrence отдаёт строго «после».
        cursor = upcoming.when + timedelta(minutes=1)
    return found


def screen_runs(cfg: Config) -> Reply:
    """Последние записи журнала прогонов."""
    db = ScheduleDb(cfg.db_path)
    records = db.recent_runs(limit=RUNS_SCREEN_LIMIT)
    if not records:
        return Reply(text="📜 Журнал прогонов пуст.", keyboard=[[_btn("◀️ Назад", "schedule")]])
    lines = ["📜 Последние прогоны", ""]
    for rec in records:
        stamp = rec.slot_at.replace("T", " ")
        tail = f" — {rec.reason}" if rec.reason else ""
        lines.append(f"{stamp}  {rec.outcome}{tail}")
    available, total = _reserve_stats(cfg)
    lines.append("")
    lines.append(f"Резерв: доступно {available} из {total}")
    return Reply(text="\n".join(lines), keyboard=[[_btn("◀️ Назад", "schedule")]])


def _reserve_stats(cfg: Config) -> tuple[int, int]:
    try:
        db = ScheduleDb(cfg.db_path)
        return len(db.list_reserve(only_available=True)), len(db.list_reserve())
    except ScheduleDbError:
        return 0, 0


# --- валидация ---------------------------------------------------------------


@dataclass
class Validation:
    """Результат проверки ввода: значение или понятная ошибка."""

    ok: bool
    value: str = ""
    error: str = ""


def validate_time(raw: str) -> Validation:
    """`HH:MM` в диапазоне 00:00–23:59. Мусор — понятная ошибка."""
    text = (raw or "").strip()
    if not text:
        return Validation(False, error="Пустое время. Нужно HH:MM, например 21:20.")
    try:
        return Validation(True, value=normalize_time(text))
    except ScheduleDbError:
        return Validation(False, error=f"«{text}» — не время. Нужно HH:MM, например 21:20.")


def validate_weekday(raw: str) -> Validation:
    text = (raw or "").strip()
    try:
        day = normalize_weekday(text)
    except ScheduleDbError:
        return Validation(
            False,
            error=f"«{text}» — не день недели. Нужно пн, вт, ср, чт, пт, сб или вс.",
        )
    return Validation(True, value=str(day))


def check_add(cfg: Config, weekday: int, time_str: str) -> Validation:
    """Проверяет, можно ли добавить слот: дубль, лимит дня."""
    db = ScheduleDb(cfg.db_path)
    existing = db.get_slot(weekday, time_str)
    if existing is not None:
        return Validation(
            False,
            error=f"Слот {WEEKDAY_NAMES[weekday]} {time_str} уже есть.",
        )
    return Validation(True, value=time_str)


def day_count(cfg: Config, weekday: int) -> int:
    return sum(1 for s in ScheduleDb(cfg.db_path).list_slots() if s.weekday == weekday)


def warn_slots_per_day(count: int) -> str:
    """Предупреждение (не запрет) при переполненном дне."""
    if count > SLOTS_PER_DAY_WARN:
        return f"\n⚠️ В этом дне теперь {count} слотов — больше {SLOTS_PER_DAY_WARN}."
    return ""


# --- команды -----------------------------------------------------------------


def handle_command(cfg: Config, text: str, now: datetime | None = None) -> Reply:
    """Разбирает команду и возвращает экран. Неизвестная — подсказку."""
    parts = (text or "").split()
    if not parts:
        return _help()
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]

    if cmd in ("/start", "/help"):
        return _help()
    if cmd == "/schedule":
        return screen_schedule(cfg, now)
    if cmd == "/next":
        return screen_preview(cfg, now)
    if cmd == "/slot":
        return _handle_slot(cfg, args, now)
    return _help()


def _help() -> Reply:
    return Reply(
        text=(
            "Команды:\n"
            "/schedule — расписание с кнопками\n"
            "/slot add <день> <HH:MM>\n"
            "/slot del <день> <HH:MM>\n"
            "/slot on|off <день> <HH:MM>\n"
            "/slot clear <день>\n"
            "/next — ближайшие 7 дней"
        ),
        keyboard=[[_btn("📅 Расписание", "schedule")]],
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


# --- кнопки ------------------------------------------------------------------


def handle_callback(cfg: Config, data: str, now: datetime | None = None) -> Reply:
    """Обрабатывает нажатие кнопки. `data` — callback_data."""
    db = ScheduleDb(cfg.db_path)
    action, _, arg = (data or "").partition(":")

    if action == "schedule":
        return screen_schedule(cfg, now)
    if action == "seed":
        db.seed_defaults()
        reply = screen_schedule(cfg, now)
        reply.text = f"Сетка заполнена: слотов {db.count_slots()}.\n\n{reply.text}"
        return reply
    if action == "preview":
        return screen_preview(cfg, now)
    if action == "runs":
        return screen_runs(cfg)
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


# --- доступ ------------------------------------------------------------------


def is_admin(cfg: Config, chat_id: int | str | None) -> bool:
    """Проверка доступа. Пустой ADMIN_CHAT_ID — админки нет ни для кого.

    Осознанно: если переменная не задана, «пускать всех» было бы дырой.
    """
    if chat_id is None or not cfg.admin_chat_id:
        return False
    return str(chat_id).strip() == str(cfg.admin_chat_id).strip()


# --- транспорт ---------------------------------------------------------------


def poll_once(cfg: Config, offset: int | None = None, now: datetime | None = None) -> tuple[int | None, int]:
    """Один цикл: забрать апдейты, ответить, вернуть (новый offset, сколько).

    Чужой чат получает отказ и запись в лог; в offset он всё равно попадает,
    иначе Telegram отдавал бы его снова и снова.
    """
    from kino import tg_api

    updates = tg_api.get_updates(cfg, offset)
    handled = 0
    for update in updates:
        offset = int(update.get("update_id", 0)) + 1
        try:
            if _dispatch(cfg, update, now):
                handled += 1
        except Exception as exc:  # noqa: BLE001 — один битый апдейт не роняет цикл
            logger.exception("админка: апдейт не обработан (%s)", exc)
    return offset, handled


def _dispatch(cfg: Config, update: dict, now: datetime | None) -> bool:
    """Обрабатывает один апдейт. False — обрабатывать было нечего.

    Возвращаемое значение нужно счётчику в логе: «обработано 5» должно
    означать пять реальных ответов, а не пять пустых апдейтов.
    """
    from kino import tg_api

    message = update.get("message")
    callback = update.get("callback_query")

    if callback:
        chat_id = (callback.get("message") or {}).get("chat", {}).get("id")
        user_id = (callback.get("from") or {}).get("id")
        callback_id = str(callback.get("id", ""))
        if not (is_admin(cfg, chat_id) or is_admin(cfg, user_id)):
            logger.warning("админка: чужое нажатие кнопки из чата %s", chat_id)
            tg_api.answer_callback(cfg, callback_id, "Нет доступа")
            return True
        tg_api.answer_callback(cfg, callback_id)
        reply = handle_callback(cfg, str(callback.get("data", "")), now)
        message_id = (callback.get("message") or {}).get("message_id")
        target = chat_id or cfg.admin_chat_id
        if message_id:
            tg_api.edit_message(cfg, target, int(message_id), reply.text, reply_markup=reply.markup())
        else:
            tg_api.send_message(cfg, target, reply.text, reply_markup=reply.markup())
        return True

    if message:
        chat_id = (message.get("chat") or {}).get("id")
        text = str(message.get("text", ""))
        if not is_admin(cfg, chat_id):
            logger.warning("админка: чужое сообщение из чата %s", chat_id)
            # Отвечаем в тот чат, откуда пришло; если id нет — в админский.
            # Пустой chat_id Telegram отвергнет, поэтому подставляем осмысленное.
            target = chat_id if chat_id is not None else cfg.admin_chat_id
            if target:
                tg_api.send_message(cfg, target, "Нет доступа")
            return True
        if chat_id is None:
            logger.debug("админка: сообщение без chat_id, пропуск")
            return False
        reply = handle_command(cfg, text, now)
        tg_api.send_message(cfg, chat_id, reply.text, reply_markup=reply.markup())
        return True

    # Ни сообщения, ни кнопки: служебный апдейт (например, edited_message).
    logger.debug("админка: апдейт без message/callback_query, пропуск")
    return False


def run(cfg: Config, *, once: bool = False, tick: float = 2.0) -> int:
    """Цикл админки. `once` — один опрос и выход (для отладки и тестов)."""
    import time

    if not cfg.admin_chat_id:
        logger.error("админка: ADMIN_CHAT_ID не задан — работать некому")
        return 1
    offset: int | None = None
    logger.info("админка запущена, чат %s", cfg.admin_chat_id)
    while True:
        try:
            offset, handled = poll_once(cfg, offset)
            if handled:
                logger.info("админка: обработано апдейтов %d", handled)
        except Exception as exc:  # noqa: BLE001 — цикл 24/7 не должен умирать
            logger.warning("админка: опрос не удался (%s)", exc)
        if once:
            return 0
        time.sleep(tick)
