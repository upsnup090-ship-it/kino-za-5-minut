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
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from kino import posting
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

#: Пауза при `409 Conflict` (другой процесс читает апдейты): старт и потолок.
#: Начинаем с минуты — второй экземпляр обычно замечают и гасят за это время;
#: потолок 15 минут нужен, чтобы ошибочно запущенный дубль не заливал лог.
CONFLICT_BACKOFF_START = 60.0
CONFLICT_BACKOFF_MAX = 900.0


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
        # Опасное — отдельной группой внизу, и только здесь (задача #77):
        # в верхнем ряду пауза дублировала бы действие, которое не должно
        # нажиматься случайно.
        keyboard.append([
            _btn("▶️ Возобновить" if paused else "⏸ Пауза публикаций", "state:pause"),
            _btn("🔄 Обновить", "nav:state:refresh"),
        ])
        keyboard.append([_btn("◀️ Свернуть", "nav:compact")])
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


# --- навигация и пауза -------------------------------------------------------


def _handle_nav(cfg: Config, arg: str, now: datetime | None) -> Reply:
    """Переход между экранами по `nav:<экран>`.

    `nav:today` — не «вчера» и не «сегодня» наугад: экран называется
    «Итог за вчера», и колбэк обязан означать ровно это (задача #78).
    Неизвестный переход возвращает главный экран, а не исключение.
    """
    if arg in ("full", "compact"):
        return screen_home(cfg, view=arg)
    if arg == "sched":
        return screen_schedule(cfg, now)
    if arg == "next7":
        return screen_preview(cfg, now)
    if arg == "reserve":
        return screen_reserve(cfg)
    if arg == "today":
        return screen_today(cfg, now)
    if arg.startswith("runs"):
        return screen_runs(cfg)
    if arg in ("state", "state:refresh"):
        return screen_home(cfg, view="full")
    return screen_home(cfg)


def _handle_state(cfg: Config, arg: str, now: datetime | None) -> Reply:
    """Действия над публикацией: `state:pause`.

    Пауза — не «пропустить слоты»: при снятии слот возвращается в работу.
    Иначе пауза на час съела бы слот до конца недели.
    """
    if arg == "pause":
        db = ScheduleDb(cfg.db_path)
        try:
            was_paused = db.is_paused()
            db.set_paused(not was_paused)
        except ScheduleDbError as exc:
            return Reply(
                text=f"❌ Не удалось переключить паузу: {exc}",
                keyboard=[[_btn("◀️ Назад", "nav:full")]],
            )
        reply = screen_home(cfg, view="full")
        head = (
            "⏸ Публикации на паузе. Слоты не публикуются и не пропускаются — "
            "после снятия вернутся в работу."
            if not was_paused
            else "▶️ Пауза снята, публикации по расписанию снова идут."
        )
        reply.text = f"{head}\n\n{reply.text}"
        return reply
    return screen_home(cfg, view="full")


def screen_reserve(cfg: Config) -> Reply:
    """Резерв: сколько постов доступно."""
    available, total = _reserve_stats(cfg)
    lines = [
        "🗄 Резерв",
        "",
        f"доступно: {available} из {total}",
        "",
        "В резерв попадает готовый пост после реальной публикации —",
        "страховка от пустого слота. Просроченные не берутся.",
    ]
    return Reply(
        text="\n".join(lines),
        keyboard=[[_btn("◀️ Назад", "nav:full")]],
    )


def screen_today(cfg: Config, now: datetime | None = None) -> Reply:
    """Итог за вчера: что вышло, что пропущено, средняя оценка.

    Считается по `runs` за прошлые сутки. Пусто — так и говорим: нулевой
    отчёт («всё хорошо, 0 постов») хуже честного «за вчера записей нет».
    """
    db = ScheduleDb(cfg.db_path)
    moment = now or datetime.now()
    yesterday = moment.date() - timedelta(days=1)

    try:
        records = db.recent_runs(limit=200)
    except ScheduleDbError:
        return Reply(
            text="📊 Итог за вчера\n\nжурнал недоступен",
            keyboard=[[_btn("◀️ Назад", "nav:full")]],
        )

    day_records: list[Any] = []
    for rec in records:
        try:
            rec_date = datetime.fromisoformat(rec.slot_at).date()
        except ValueError:
            continue
        if rec_date == yesterday:
            day_records.append(rec)

    if not day_records:
        return Reply(
            text=(
                f"📊 Итог за {yesterday.strftime('%d.%m')}\n\n"
                "записей нет — не было ни публикаций, ни пропусков"
            ),
            keyboard=[[_btn("◀️ Назад", "nav:full")]],
        )

    published = [r for r in day_records if r.outcome == "published"]
    skipped = [r for r in day_records if r.outcome == "skipped"]
    failed = [r for r in day_records if r.outcome == "failed"]
    qualities = [r.quality_score for r in published if r.quality_score is not None]

    lines = [f"📊 Итог за {yesterday.strftime('%d.%m')}", ""]
    lines.append(f"опубликовано: {len(published)}")
    if skipped:
        lines.append(f"пропущено: {len(skipped)}")
    if failed:
        lines.append(f"сбоев: {len(failed)}")
    if qualities:
        avg = sum(qualities) / len(qualities)
        lines.append(f"средняя оценка: {avg:.1f}")
    lines.append("")
    for rec in published:
        stamp = rec.slot_at.replace("T", " ")[11:16]
        tail = f"{stamp} · {rec.format or '—'}"
        if rec.caption_len:
            tail += f" · {rec.caption_len} симв."
        if rec.quality_score is not None:
            tail += f" · q{rec.quality_score:g}"
        if rec.is_manual:
            tail += " · ✋"
        lines.append(tail)
    return Reply(
        text="\n".join(lines),
        keyboard=[[_btn("◀️ Назад", "nav:full")]],
    )


# --- постинг по кнопке -------------------------------------------------------


def handle_post_button(cfg: Config, mode: str, now: datetime | None = None) -> Reply:
    """Кнопка постинга: `test` — в личку, `final` — в канал.

    Прогон долгий (сбор + LLM + судья, до `deadline_seconds`), поэтому он
    уходит в отдельный поток, а кнопка сразу отвечает «запущено». Бот не
    замирает, Telegram не отдаёт таймаут, кнопка не залипает.

    Второй тап во время прогона получает отказ: два поста в канал — это не
    то, чего ждёт человек, нажавший кнопку дважды.
    """
    if mode not in ("test", "final"):
        return screen_home(cfg)

    if not posting.STATE.begin(mode):
        return Reply(
            text=(
                f"⏳ Уже идёт прогон ({posting.STATE.mode}).\n"
                "Дождись его — второй пост в канал не нужен."
            ),
            keyboard=[[_btn("🏠 Домой", "home")]],
        )

    where = "в личку" if mode == "test" else "в канал"
    logger.info("постинг по кнопке: %s %s", mode, where)

    def _worker() -> None:
        from kino import tg_api

        try:
            if mode == "test":
                prepared = posting.build_only(cfg)
                head = posting.preview_text(prepared, to_channel=False)
                tg_api.send_message(cfg, cfg.admin_chat_id, head)
                if prepared.ok:
                    ok = posting.deliver(cfg, prepared, cfg.admin_chat_id)
                    if not ok:
                        tg_api.send_message(
                            cfg, cfg.admin_chat_id,
                            "⚠️ Пост собран, но отправить не удалось — смотри лог.",
                        )
                else:
                    tg_api.send_message(cfg, cfg.admin_chat_id, head)
            else:
                result = posting.publish_now(cfg)
                tg_api.send_message(
                    cfg, cfg.admin_chat_id,
                    posting.describe_result(result, to_channel=True),
                )
        except Exception as exc:  # noqa: BLE001 — поток не должен умирать молча
            logger.exception("постинг по кнопке упал: %s", exc)
            try:
                tg_api.send_message(
                    cfg, cfg.admin_chat_id, f"❌ Прогон упал: {exc.__class__.__name__}"
                )
            except Exception:  # noqa: BLE001
                logger.error("не удалось сообщить о падении прогона")
        finally:
            posting.STATE.end()

    threading.Thread(target=_worker, name=f"post-{mode}", daemon=True).start()

    if mode == "test":
        text = "🧪 Запустил тестовый пост. Соберу и пришлю сюда же — в канал не пойдёт."
    else:
        text = "🚀 Запустил финальный пост. Уйдёт в канал, как по расписанию."
    return Reply(text=text, keyboard=[[_btn("🏠 Домой", "home")]])


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
    """Цикл админки. `once` — один опрос и выход (для отладки и тестов).

    Две разные реакции на сбой опроса:

    - **`409 Conflict`** — другой процесс уже читает апдейты этим токеном.
      Повтор каждые две секунды не поможет и только засоряет лог, поэтому
      первое сообщение пишется как ERROR с объяснением, дальше — молчание,
      и backoff растёт до `CONFLICT_BACKOFF_MAX`. Как только опрос снова
      пошёл — пишем, что конфликт ушёл.
    - **всё остальное** — обычный WARNING и короткая пауза: сеть моргнула,
      Telegram ответил 502, прокси лёг. Это проходит само.
    """
    import time

    from kino.tg_api import TgConflict

    if not cfg.admin_chat_id:
        logger.error("админка: ADMIN_CHAT_ID не задан — работать некому")
        return 1
    offset: int | None = None
    logger.info("админка запущена, чат %s", cfg.admin_chat_id)

    #: Растёт вдвое на каждом подряд идущем конфликте, сбрасывается успехом.
    conflict_backoff = CONFLICT_BACKOFF_START
    #: Сообщение печатается один раз на серию, а не на каждую попытку.
    conflict_reported = False

    while True:
        try:
            offset, handled = poll_once(cfg, offset)
            if handled:
                logger.info("админка: обработано апдейтов %d", handled)
            if conflict_reported:
                logger.info("админка: 409 ушёл, опрос снова работает")
                conflict_reported = False
            conflict_backoff = CONFLICT_BACKOFF_START
        except TgConflict as exc:
            if not conflict_reported:
                logger.error(
                    "админка: %s Ждём до %.0fс между попытками. "
                    "Проверь: `systemctl status kino-admin` и не запущен ли "
                    "второй экземпляр вручную.",
                    exc,
                    exc.retry_after,
                )
                conflict_reported = True
            else:
                logger.debug("админка: 409 всё ещё держится")
            if once:
                return 1
            time.sleep(min(conflict_backoff, CONFLICT_BACKOFF_MAX))
            conflict_backoff = min(conflict_backoff * 2, CONFLICT_BACKOFF_MAX)
            continue
        except Exception as exc:  # noqa: BLE001 — цикл 24/7 не должен умирать
            logger.warning("админка: опрос не удался (%s)", exc)
        if once:
            return 0
        time.sleep(tick)
