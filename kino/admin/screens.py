"""Экраны админки: чистые функции «состояние → Reply».

Ни сети, ни Telegram: каждый экран принимает `Config` и возвращает текст с
клавиатурой.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from kino.config import Config
from kino.schedule import SlotConfig, next_occurrence
from kino.schedule_db import WEEKDAY_NAMES, ScheduleDb, ScheduleDbError

from kino.admin.common import Reply, _btn


def screen_home(cfg: Config) -> Reply:
    return Reply(text="👋 Приветствие\n\nВыбери раздел:", keyboard=[
        [_btn("🤖 Автопостинг ТГ", "nav:autoposting")],
        [_btn("ℹ️ О боте", "nav:about")],
        [_btn("🗄 Бэкап+логи", "nav:backup")],
    ])


def screen_autoposting(cfg: Config) -> Reply:
    db = ScheduleDb(cfg.db_path)
    paused = _safe_paused(db)
    lines = ["🤖 Автопостинг ТГ", "", _serve_state_line(db), _last_run_line(db), f"резерв: {_reserve_stats(cfg)[0]} из {_reserve_stats(cfg)[1]}"]
    if paused:
        lines.append("⏸ ПУБЛИКАЦИИ НА ПАУЗЕ")
    return Reply(text="\n".join(lines), keyboard=[
        [_btn("📡 Состояние", "nav:state"), _btn("📜 Ошибки", "nav:state:errors")],
        [_btn("⏸ Пауза публикаций", "state:pause"), _btn("▶️ Запустить сейчас", "nav:runnow")],
        [_btn("📅 Расписание", "nav:sched"), _btn("🗂 Прогоны", "nav:runs:0")],
        [_btn("◀️ Назад", "nav:home")],
    ])


def screen_about(cfg: Config) -> Reply:
    return Reply(text="ℹ️ О боте\n\nБот автопубликаций проекта «Кино за 5 минут».\nОн собирает материалы, готовит посты и публикует их по расписанию.\n\nАдминка доступна только в чате владельца.", keyboard=[[_btn("◀️ Назад", "nav:home")]])


def screen_backup(cfg: Config) -> Reply:
    return Reply(text="🗄 Бэкап+логи\n\nРаздел для служебных действий с логами и резервными копиями.", keyboard=[
        [_btn("📜 Логи", "nav:backup:logs"), _btn("💾 Бэкап", "nav:backup:make")],
        [_btn("◀️ Назад", "nav:home")],
    ])


def _safe_paused(db: Any) -> bool:
    try:
        return bool(db.is_paused())
    except (ScheduleDbError, AttributeError):
        return False


def _last_run_line(db: Any) -> str:
    try:
        records = db.recent_runs(limit=1)
    except (ScheduleDbError, AttributeError):
        return "последний прогон: нет данных"
    if not records:
        return "последний прогон: ещё не было"
    rec = records[0]
    stamp = rec.slot_at.replace("T", " ")[:16]
    tail = f"{stamp} · {rec.outcome}"
    if rec.format:
        tail += f" · {rec.format}"
    if rec.quality_score is not None:
        tail += f" · q{rec.quality_score:g}"
    if rec.is_manual:
        tail += " · ✋"
    return f"последний: {tail}"


def _serve_state_line(db: Any) -> str:
    from kino.schedule_db import META_SERVE_MODE, META_SERVE_STARTED, ScheduleDbError
    try:
        mode = db.get_meta(META_SERVE_MODE)
        started = db.get_meta(META_SERVE_STARTED)
    except (ScheduleDbError, AttributeError):
        return "цикл: состояние неизвестно"
    if mode is None:
        return "цикл: не запущен"
    label = "боевой" if mode == "prod" else "тестовый (dry-run)"
    if started:
        stamp = started.replace("T", " ")[:16]
        return f"цикл: {label}, с {stamp}"
    return f"цикл: {label}"


def screen_schedule(cfg: Config, now: datetime | None = None) -> Reply:
    db = ScheduleDb(cfg.db_path)
    slots = db.list_slots()
    if not slots:
        return Reply(text="📅 Расписание пусто.\nЗаполнить сеткой владельца (13 слотов)?", keyboard=[[_btn("🌱 Заполнить сетку", "seed"), _btn("◀️ Назад", "nav:autoposting")]])
    lines = ["📅 Расписание публикаций", f"слотов: {len(slots)} (включено {sum(1 for s in slots if s.enabled)})", ""]
    keyboard: list[list[dict[str, str]]] = []
    for day in range(7):
        day_slots = [s for s in slots if s.weekday == day]
        stamps = ", ".join(f"{s.time}{'' if s.enabled else ' ⏸'}" for s in day_slots) or "—"
        lines.append(f"{WEEKDAY_NAMES[day]}: {stamps}")
        row = [_btn(f"{WEEKDAY_NAMES[day]} {s.time}{'' if s.enabled else '⏸'}", f"slot:{s.id}") for s in day_slots]
        if row:
            keyboard.append(row)
    keyboard.append([_btn("➕ Добавить слот", "add")])
    keyboard.append([_btn("👁 Ближайшие 7 дней", "preview"), _btn("📜 Прогоны", "runs")])
    keyboard.append([_btn("◀️ Назад", "nav:autoposting")])
    return Reply(text="\n".join(lines), keyboard=keyboard)


def screen_slot(cfg: Config, slot_id: int) -> Reply:
    db = ScheduleDb(cfg.db_path)
    slot = next((s for s in db.list_slots() if s.id == slot_id), None)
    if slot is None:
        return screen_schedule(cfg)
    last = ""
    try:
        runs = [r for r in db.recent_runs(limit=50) if r.slot_id == slot_id and r.finished_at]
    except ScheduleDbError:
        runs = []
    if runs:
        rec = runs[0]
        stamp = rec.slot_at.replace("T", " ")
        tail = f" — {rec.reason}" if rec.reason else ""
        last = f"\nПоследний прогон: {stamp} → {rec.outcome}{tail}"
    else:
        last = "\nПрогонов ещё не было"
    text = f"🕐 Слот {slot.weekday_name} {slot.time}\nСостояние: {'включён' if slot.enabled else 'выключен'}{last}"
    toggle = "⏸ Выключить" if slot.enabled else "▶️ Включить"
    toggle_data = f"off:{slot.id}" if slot.enabled else f"on:{slot.id}"
    return Reply(text=text, keyboard=[[ _btn(toggle, toggle_data), _btn("✏️ Изменить время", f"edit:{slot_id}") ], [_btn("🗑 Удалить", f"del:{slot_id}")], [_btn("◀️ Назад", "schedule")]])


def screen_preview(cfg: Config, now: datetime | None = None) -> Reply:
    db = ScheduleDb(cfg.db_path)
    slots = [(s.id, s.weekday, s.time) for s in db.list_slots(enabled_only=True)]
    if not slots:
        return Reply(text="Слотов нет — публиковать нечего.", keyboard=[[_btn("◀️ Назад", "nav:autoposting")]])
    slot_cfg = SlotConfig(catchup_minutes=cfg.settings.schedule_catchup_minutes, late_grace_minutes=cfg.settings.schedule_late_grace_minutes, tz=cfg.settings.timezone)
    moment = slot_cfg.local_now(now)
    lines = ["👁 Ближайшие 7 дней", ""]
    horizon = moment + timedelta(days=7)
    upcoming_all = _upcoming_list(slots, slot_cfg, moment, horizon)
    for upcoming in upcoming_all:
        lines.append(f"{upcoming.when.strftime('%d.%m')} {WEEKDAY_NAMES[upcoming.weekday]}  {upcoming.time}")
    if not upcoming_all:
        lines.append("в ближайшие 7 дней слотов нет")
    else:
        lines.append("")
        lines.append(f"итого: {len(upcoming_all)}")
    return Reply(text="\n".join(lines), keyboard=[[_btn("◀️ Назад", "nav:autoposting")]])


def _upcoming_list(slots: list[tuple[int, int, str]], slot_cfg: SlotConfig, moment: datetime, horizon: datetime) -> list[Any]:
    found: list[Any] = []
    cursor = moment
    while len(found) <= 50:
        upcoming = next_occurrence(cursor, slots, slot_cfg)
        if upcoming is None or upcoming.when > horizon:
            break
        found.append(upcoming)
        cursor = upcoming.when + timedelta(minutes=1)
    return found


def screen_runs(cfg: Config) -> Reply:
    db = ScheduleDb(cfg.db_path)
    records = db.recent_runs(limit=20)
    if not records:
        return Reply(text="📜 Журнал прогонов пуст.", keyboard=[[_btn("◀️ Назад", "nav:autoposting")]])
    lines = ["📜 Последние прогоны", ""]
    for rec in records:
        lines.append(_format_run_line(rec))
    available, total = _reserve_stats(cfg)
    lines.append("")
    lines.append(f"Резерв: доступно {available} из {total}")
    return Reply(text="\n".join(lines), keyboard=[[_btn("◀️ Назад", "nav:autoposting"), _btn("🏠 Домой", "nav:home")]])


def _format_run_line(rec: Any) -> str:
    stamp = rec.slot_at.replace("T", " ")
    parts = [f"{stamp}  {rec.outcome}"]
    if rec.is_manual:
        parts.append("✋")
    if rec.format:
        parts.append(rec.format)
    if rec.caption_len:
        parts.append(f"{rec.caption_len} симв.")
    if rec.quality_score is not None:
        parts.append(f"оценка {rec.quality_score:g}")
    tail = " · ".join(parts)
    if rec.reason:
        tail = f"{tail} — {rec.reason}"
    return tail


def _reserve_stats(cfg: Config) -> tuple[int, int]:
    try:
        db = ScheduleDb(cfg.db_path)
        return len(db.list_reserve(only_available=True)), len(db.list_reserve())
    except ScheduleDbError:
        return 0, 0
