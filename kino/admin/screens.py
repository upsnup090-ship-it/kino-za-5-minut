"""Экраны админки: чистые функции «состояние → Reply».

Ни сети, ни Telegram: каждый экран принимает `Config` и возвращает текст с
клавиатурой. Это то, что проверяют тесты, и то, что переиспользуют команды
и колбэки — два пути к одному состоянию.

Превью «ближайшие 7 дней» считается теми же функциями планировщика
(`next_occurrence`), а не отдельной копией логики: иначе экран и цикл
разойдутся.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from kino.config import Config
from kino.schedule import SlotConfig, next_occurrence
from kino.schedule_db import WEEKDAY_NAMES, ScheduleDb, ScheduleDbError

from kino.admin.common import RUNS_SCREEN_LIMIT, Reply, _btn

# --- экраны ------------------------------------------------------------------


def screen_home(cfg: Config, view: str = "compact") -> Reply:
    """Главный экран — S0 из спецификации, два режима рендера.

    Не два разных экрана, а один с двумя наборами блоков (задача #74):

    - **compact** — что нужно каждый день: режим, цикл, резерв, четыре
      кнопки. Задача — за секунду понять, жив ли бот.
    - **full** — то же плюс группы: «Управление расписанием», «Контент»,
      «Действия». Опасное внизу: пауза и ручной прогон не должны стоять
      рядом с «Расписание», куда жмут каждый день.

    В блоке состояния обязательно режим цикла: `prod` или `dry-run`
    (задача #75). Тестовый запуск на сервере не должен выглядеть боевым.
    """
    db = ScheduleDb(cfg.db_path)
    enabled = db.list_slots(enabled_only=True)
    total_slots = len(db.list_slots())
    mode = cfg.settings.schedule_mode
    available, total = _reserve_stats(cfg)
    paused = _safe_paused(db)

    lines = ["🎬 Кино за 5 минут" + (" · админка" if view == "full" else ""), ""]
    lines.append(_serve_state_line(db))
    lines.append(_last_run_line(db))
    lines.append(f"резерв: {available} из {total}")
    if paused:
        lines.append("⏸ ПУБЛИКАЦИИ НА ПАУЗЕ")

    if view == "full":
        if mode == "slots":
            lines.append(f"режим расписания: сетка слотов")
            lines.append(f"слотов в неделю: {total_slots} · включено: {len(enabled)}")
        else:
            windows = ", ".join(str(w) for w in cfg.settings.schedule_windows)
            lines.append(f"режим расписания: окна {windows}")
        lines.append(f"часовой пояс: {cfg.settings.timezone}")
        lines.append("")
        lines.append("Тестовый пост — в личку, канал и state не трогаются.")
        lines.append("Финальный — в канал, как по расписанию.")

    keyboard: list[list[dict[str, str]]] = [
        [_btn("🧪 Тестовый пост", "post:test"), _btn("🚀 Финальный пост", "post:final")],
        [_btn("📅 Расписание", "nav:sched"), _btn("🗂 Прогоны", "nav:runs:0")],
    ]
    if view == "compact":
        keyboard.append([_btn("⋯ Подробнее", "nav:full")])
    else:
        keyboard.append([_btn("👁 7 дней", "nav:next7"), _btn("🗄 Резерв", "nav:reserve")])
        keyboard.append([_btn("📊 Итог за вчера", "nav:today")])
        keyboard.append([_btn("📡 Состояние", "nav:state"), _btn("📜 Ошибки", "nav:state:errors")])
        # Опасное — отдельной группой внизу, и только здесь (задача #77):
        # в верхнем ряду пауза дублировала бы действие, которое не должно
        # нажиматься случайно.
        keyboard.append([
            _btn("▶️ Возобновить" if paused else "⏸ Пауза публикаций", "state:pause"),
            _btn("▶️ Запустить сейчас", "nav:runnow"),
        ])
        keyboard.append([_btn("🔄 Обновить", "nav:state:refresh"), _btn("◀️ Свернуть", "nav:compact")])
    return Reply(text="\n".join(lines), keyboard=keyboard)


def _safe_paused(db: Any) -> bool:
    """Пауза из БД; недоступная БД — «не на паузе», а не падение экрана."""
    try:
        return bool(db.is_paused())
    except (ScheduleDbError, AttributeError):
        return False


def _last_run_line(db: Any) -> str:
    """Последний прогон одной строкой: когда, исход, формат, оценка."""
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
    """Строка о публикующем цикле: `prod`/`dry-run`, когда стартовал.

    Цикл может быть не запущен вовсе — тогда честнее сказать «цикл не
    запущен», чем показывать `prod` по умолчанию: экран не должен врать
    о том, что посты выходят сами.
    """
    from kino.schedule_db import (
        META_SERVE_MODE,
        META_SERVE_STARTED,
        ScheduleDbError,
    )

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
    """Экран публикаций по расписанию: сетка по дням, каждый слот — кнопка."""
    db = ScheduleDb(cfg.db_path)
    slots = db.list_slots()
    if not slots:
        return Reply(
            text="📅 Расписание пусто.\nЗаполнить сеткой владельца (13 слотов)?",
            keyboard=[[_btn("🌱 Заполнить сетку", "seed"), _btn("◀️ Назад", "home")]],
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
    keyboard.append([_btn("◀️ Назад", "home")])
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
    """Последние записи журнала прогонов.

    Строка рендерится из одной таблицы `runs`: исход, формат, длина подписи,
    оценка судьи, признак ручного запуска. Читать CSV статистики и логи для
    этого экрана больше не нужно — за это отвечала задача #69.
    """
    db = ScheduleDb(cfg.db_path)
    records = db.recent_runs(limit=RUNS_SCREEN_LIMIT)
    if not records:
        return Reply(text="📜 Журнал прогонов пуст.", keyboard=[[_btn("◀️ Назад", "schedule")]])
    lines = ["📜 Последние прогоны", ""]
    for rec in records:
        lines.append(_format_run_line(rec))
    available, total = _reserve_stats(cfg)
    lines.append("")
    lines.append(f"Резерв: доступно {available} из {total}")
    return Reply(
        text="\n".join(lines),
        keyboard=[[_btn("◀️ Назад", "schedule"), _btn("🏠 Домой", "home")]],
    )


def _format_run_line(rec: Any) -> str:
    """Одна строка журнала: время, исход, формат, длина, оценка, признак ручного.

    Ручной прогон помечается `✋` и причиной: в журнале «пост по кнопке в 14:30»
    не должен читаться как сработавший слот. Причина печатается, если она
    есть и не совпадает с исходом.
    """
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

