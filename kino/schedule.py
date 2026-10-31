"""Расписание публикаций: окна, суточный лимит, пауза между постами.

Чистая логика без побочных эффектов: время передаётся параметром, а не
берётся из `datetime.now()` внутри. Иначе тесты пришлось бы писать со сном,
а поведение зависело бы от момента запуска.

Модуль отвечает на один вопрос: **пора ли публиковать прямо сейчас** — и на
обратный: **когда проверять в следующий раз**. Всё остальное (что именно
постить) решает pipeline.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

#: "11:00-18:00" или "22:50" — интервал либо точное время.
_WINDOW_RE = re.compile(
    r"^(?P<h1>\d{1,2}):(?P<m1>\d{2})"
    r"(?:\s*-\s*(?P<h2>\d{1,2}):(?P<m2>\d{2}))?$"
)

DEFAULT_TIMEZONE = "Europe/Moscow"


class ScheduleError(Exception):
    """Ошибка расписания: мусор в конфиге или негодная зона."""


@dataclass(frozen=True)
class Window:
    """Окно публикации: [start, end) в локальном времени.

    Точка без интервала (например 22:50) — окно нулевой длины: пост уходит
    ровно в эту минуту, а не «когда попало в течение часа».
    """

    start: time
    end: time

    @property
    def is_point(self) -> bool:
        """Точное время, а не интервал."""
        return self.start == self.end

    def __str__(self) -> str:
        if self.is_point:
            return f"{self.start:%H:%M}"
        return f"{self.start:%H:%M}-{self.end:%H:%M}"

    def contains(self, moment: datetime) -> bool:
        """Попадает ли момент в окно.

        Для точки — совпадение часа и минуты. Для интервала — [start, end).
        Окно, переходящее через полночь (22:00-02:00), поддерживается.
        """
        current = moment.time()
        if self.is_point:
            return current.hour == self.start.hour and current.minute == self.start.minute
        if self.start < self.end:
            return self.start <= current < self.end
        # Через полночь: [start, 24:00) или [00:00, end).
        return current >= self.start or current < self.end

    def next_start_after(self, moment: datetime) -> datetime:
        """Ближайшее начало этого окна после момента (сегодня или завтра)."""
        candidate = moment.replace(
            hour=self.start.hour,
            minute=self.start.minute,
            second=0,
            microsecond=0,
        )
        if candidate <= moment:
            candidate += timedelta(days=1)
        return candidate


@dataclass
class ScheduleConfig:
    """Настройки расписания, собранные из конфига."""

    windows: list[Window] = field(default_factory=list)
    max_posts_per_day: int = 3
    min_gap_minutes: int = 45
    tz: str = DEFAULT_TIMEZONE

    def __post_init__(self) -> None:
        if not self.windows:
            raise ScheduleError("расписание: список окон пуст")
        if self.max_posts_per_day < 1:
            raise ScheduleError(
                f"расписание: max_posts_per_day={self.max_posts_per_day}, нужно >= 1"
            )
        if self.min_gap_minutes < 0:
            raise ScheduleError(
                f"расписание: min_gap_minutes={self.min_gap_minutes}, нужно >= 0"
            )

    @property
    def zone(self) -> ZoneInfo:
        """Зона расписания. Негодная зона — понятная ошибка, не трейсбек."""
        try:
            return ZoneInfo(self.tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ScheduleError(
                f"расписание: неизвестная зона {self.tz!r}. "
                "Проверь schedule.timezone или поставь tzdata."
            ) from exc

    def local_now(self, moment: datetime | None = None) -> datetime:
        """Момент в локальной зоне расписания.

        Naive-время считаем уже локальным (так удобнее в тестах и при вызове
        из кода, где время берут из `datetime.now()`).
        """
        if moment is None:
            return datetime.now(self.zone)
        if moment.tzinfo is None:
            return moment.replace(tzinfo=self.zone)
        return moment.astimezone(self.zone)


@dataclass
class DayState:
    """Что уже сделано в текущий день. Живёт в state, переживает рестарт."""

    date: str = ""          # YYYY-MM-DD в локальной зоне
    posts: int = 0          # сколько опубликовано за этот день
    last_post_at: str = ""  # ISO локального времени последнего поста


def parse_windows(raw: list[str] | tuple[str, ...]) -> list[Window]:
    """["11:00-18:00", "22:50"] -> [Window(11:00,18:00), Window(22:50,22:50)].

    Бросает ScheduleError на мусор: опечатка в конфиге должна падать на старте,
    а не превращаться в «бот молчит сутки».
    """
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ScheduleError("расписание: windows должен быть непустым списком")

    windows: list[Window] = []
    for entry in raw:
        if not isinstance(entry, str):
            raise ScheduleError(f"расписание: окно {entry!r} — не строка")
        text = entry.strip()
        match = _WINDOW_RE.match(text)
        if not match:
            raise ScheduleError(
                f"расписание: не разобрать окно {entry!r}. "
                'Ожидается "HH:MM" или "HH:MM-HH:MM".'
            )
        start = _make_time(match.group("h1"), match.group("m1"), entry)
        if match.group("h2") is None:
            windows.append(Window(start=start, end=start))
            continue
        end = _make_time(match.group("h2"), match.group("m2"), entry)
        if start == end:
            raise ScheduleError(
                f"расписание: окно {entry!r} нулевой длины. "
                'Для точного времени пиши без дефиса: "22:50".'
            )
        windows.append(Window(start=start, end=end))

    windows.sort(key=lambda w: (w.start.hour, w.start.minute))
    return windows


def _make_time(hours: str, minutes: str, source: str) -> time:
    """Собирает time с проверкой диапазона (в конфиге бывает 25:00)."""
    hour = int(hours)
    minute = int(minutes)
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        raise ScheduleError(f"расписание: недопустимое время в окне {source!r}")
    return time(hour=hour, minute=minute)


def is_window_open(now: datetime, window: Window) -> bool:
    """Открыто ли окно в этот момент (для точечных — совпадение минуты)."""
    return window.contains(now)


def open_window(now: datetime, schedule: ScheduleConfig) -> Window | None:
    """Первое открытое окно в этот момент, иначе None."""
    for window in schedule.windows:
        if window.contains(now):
            return window
    return None


def _reset_if_new_day(now: datetime, day: DayState, schedule: ScheduleConfig) -> DayState:
    """Новый день — счётчик с нуля. Возвращает актуальное состояние дня.

    Дату сравниваем в локальной зоне расписания: иначе на границе суток
    UTC и Москва разойдутся на три часа.
    """
    today = schedule.local_now(now).strftime("%Y-%m-%d")
    if day.date != today:
        return DayState(date=today, posts=0, last_post_at="")
    return day


def _minutes_since_last_post(now: datetime, day: DayState) -> float | None:
    """Сколько минут прошло с последнего поста. None, если постов не было."""
    if not day.last_post_at:
        return None
    try:
        last = datetime.fromisoformat(day.last_post_at)
    except ValueError:
        logger.warning("state: не разобрать last_post_at=%r, пауза не проверяется", day.last_post_at)
        return None
    if last.tzinfo is None:
        return None
    return (now - last).total_seconds() / 60.0


def effective_gap_minutes(schedule: ScheduleConfig) -> int:
    """Минимальный интервал с учётом длины окон и суточного лимита.

    Фиксированный `min_gap_minutes` даёт кластеризацию: при окне 11:00-18:00
    (420 мин), лимите 3 и паузе 45 мин бот публикует всё к 12:30 и молчит до
    вечера. Поэтому интервал растягивается до «длина окна / постов в окне»:

        дней в окне ≈ max_posts_per_day (вечерняя точка считается отдельно)
        интервал = длина_окна / постов_в_окне

    Возвращается максимум из заданного и вычисленного — ручной минимум
    остаётся жёстким полом, автоматика только растягивает.
    """
    interval_windows = [w for w in schedule.windows if not w.is_point]
    if not interval_windows:
        return schedule.min_gap_minutes

    # Сколько постов приходится на интервальные окна: вычитаем точки,
    # каждая точка — ровно один пост.
    points = sum(1 for w in schedule.windows if w.is_point)
    interval_posts = max(1, schedule.max_posts_per_day - points)

    span = sum(_window_minutes(w) for w in interval_windows)
    if span <= 0:
        return schedule.min_gap_minutes

    computed = int(span / interval_posts)
    return max(schedule.min_gap_minutes, computed)


def _window_minutes(window: Window) -> int:
    """Длина окна в минутах (через полночь считается корректно)."""
    start = window.start.hour * 60 + window.start.minute
    end = window.end.hour * 60 + window.end.minute
    if end > start:
        return end - start
    return (24 * 60 - start) + end


def should_publish(now: datetime, schedule: ScheduleConfig, day: DayState) -> bool:
    """Пора ли публиковать прямо сейчас.

    Проверки по порядку, каждая может отказать:

    1. Новый день — счётчик сбрасывается (это не отказ, а подготовка).
    2. Лимит постов в сутки исчерпан.
    3. Момент не попадает ни в одно окно.
    4. С последнего поста прошло меньше интервала (см. `effective_gap_minutes`).
    """
    local = schedule.local_now(now)
    actual = _reset_if_new_day(local, day, schedule)

    if actual.posts >= schedule.max_posts_per_day:
        logger.debug("расписание: лимит %d постов на %s исчерпан", schedule.max_posts_per_day, actual.date)
        return False

    window = open_window(local, schedule)
    if window is None:
        logger.debug("расписание: %s вне окон публикации", local.strftime("%H:%M"))
        return False

    gap = _minutes_since_last_post(local, actual)
    required = effective_gap_minutes(schedule)
    if gap is not None and gap < required:
        logger.debug(
            "расписание: с прошлого поста %.0f мин < %d, рано",
            gap,
            required,
        )
        return False

    return True


def next_slot(
    now: datetime,
    schedule: ScheduleConfig,
    day: DayState,
) -> datetime | None:
    """Когда можно публиковать в следующий раз.

    None — сегодня больше нельзя (лимит исчерпан или окна кончились),
    ждать до начала следующего окна следующего дня.
    """
    local = schedule.local_now(now)
    actual = _reset_if_new_day(local, day, schedule)

    if actual.posts >= schedule.max_posts_per_day:
        return _first_window_tomorrow(local, schedule)

    required = effective_gap_minutes(schedule)

    # Если окно уже открыто и пауза прошла — можно прямо сейчас.
    # Без этой ветки next_slot внутри окна возвращал начало следующего окна,
    # и цикл serve спал бы до вечера вместо публикации.
    if open_window(local, schedule) is not None:
        gap = _minutes_since_last_post(local, actual)
        if gap is None or gap >= required:
            return local

    # Ближайшее начало окна, начиная с текущего момента.
    candidates: list[datetime] = []
    for window in schedule.windows:
        start = window.next_start_after(local)
        candidates.append(start)
    if not candidates:
        return None

    earliest = min(candidates)

    # Пауза может сдвинуть ближайший слот позже начала окна.
    gap = _minutes_since_last_post(local, actual)
    if gap is not None:
        wait_until = local + timedelta(minutes=required - gap)
        if wait_until > earliest:
            # Сдвинутый слот должен попасть в какое-то окно, иначе берём
            # следующее начало окна после него.
            if open_window(wait_until, schedule) is not None:
                return wait_until
            later = [w.next_start_after(wait_until) for w in schedule.windows]
            return min(later) if later else None

    return earliest


def _first_window_tomorrow(now: datetime, schedule: ScheduleConfig) -> datetime:
    """Начало первого окна следующего дня."""
    tomorrow = now + timedelta(days=1)
    starts = [
        tomorrow.replace(hour=w.start.hour, minute=w.start.minute, second=0, microsecond=0)
        for w in schedule.windows
    ]
    return min(starts)


def apply_post(day: DayState, now: datetime, schedule: ScheduleConfig) -> DayState:
    """Отмечает состоявшуюся публикацию: +1 к счётчику, время последнего поста.

    Возвращает новое состояние дня — вызывающий сохраняет его в state.
    """
    local = schedule.local_now(now)
    actual = _reset_if_new_day(local, day, schedule)
    return DayState(
        date=actual.date,
        posts=actual.posts + 1,
        last_post_at=local.isoformat(),
    )


def describe_day(day: DayState, schedule: ScheduleConfig) -> str:
    """Строка для лога: сколько постов и когда последний."""
    if not day.date:
        return "сегодня постов не было"
    if not day.last_post_at:
        return f"постов {day.posts}/{schedule.max_posts_per_day}"
    try:
        last = datetime.fromisoformat(day.last_post_at)
        stamp = last.strftime("%H:%M")
    except ValueError:
        stamp = "?"
    return f"постов {day.posts}/{schedule.max_posts_per_day}, последний в {stamp}"

# =============================================================================
# Режим слотов (шаг 8)
# =============================================================================
#
# Сетка день-время ломает обе опоры старого режима: слот — точка, а не
# интервал, и суточный лимит задан самой сеткой. Здесь — чистые функции над
# списком слотов: время передаётся параметром, `datetime.now()` внутри нет.
# Хранение (SQLite) живёт в `kino/schedule_db.py`, вызов — в `kino/serve.py`.
#
# Точность: тик 60 с и слот 11:05 дают потерю слота, если тик пришёлся на
# 11:04:50. Поэтому слот «наступил» не в эту минуту, а в интервале
# [slot, slot + catchup_minutes). Отметка об отработке — в таблице `runs`,
# повторного срабатывания нет.


@dataclass(frozen=True)
class SlotConfig:
    """Настройки режима слотов из конфига.

    Отдельно от `ScheduleConfig`, потому что в режиме slots окна, суточный
    лимит и пауза не участвуют: сетка сама расставляет ритм.
    """

    catchup_minutes: int = 30
    late_grace_minutes: int = 20
    tz: str = DEFAULT_TIMEZONE

    def __post_init__(self) -> None:
        if self.catchup_minutes <= 0:
            raise ScheduleError(
                f"слоты: catchup_minutes={self.catchup_minutes}, нужно > 0"
            )
        if self.late_grace_minutes < 0:
            raise ScheduleError(
                f"слоты: late_grace_minutes={self.late_grace_minutes}, нужно >= 0"
            )

    @property
    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ScheduleError(
                f"слоты: неизвестная зона {self.tz!r}. "
                "Проверь schedule.timezone или поставь tzdata."
            ) from exc

    def local_now(self, moment: datetime | None = None) -> datetime:
        """Момент в локальной зоне. Naive считаем уже локальным (как в окнах)."""
        if moment is None:
            return datetime.now(self.zone)
        if moment.tzinfo is None:
            return moment.replace(tzinfo=self.zone)
        return moment.astimezone(self.zone)


@dataclass(frozen=True)
class SlotOccurrence:
    """Слот, привязанный к конкретной дате: `пт 23:40` 31.10.2026.

    `slot_at` — ISO без таймзоны (`2026-10-31T23:40`): это ключ в `runs`,
    и он не должен зависеть от того, в какой зоне процесс смотрит на время.
    """

    slot_id: int
    weekday: int
    time: str
    date: str      # YYYY-MM-DD в локальной зоне
    slot_at: str   # YYYY-MM-DDTHH:MM

    def __str__(self) -> str:
        return f"{self.date} {self.time}"

    @property
    def when(self) -> datetime:
        """Момент слота в локальной зоне (aware).

        `slot_at` хранится без таймзоны — это ключ в `runs`. Но `when`
        обязан быть aware: смешивание naive и aware давало `TypeError` в
        `lateness_minutes` (шаг 8) и в превью админки (шаг 10). Один раз
        правильно здесь — и ни один вызывающий не должен об этом думать.
        """
        moment = datetime.fromisoformat(self.slot_at)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        return moment


def _parse_hhmm(value: str) -> tuple[int, int]:
    """`'11:05'` -> (11, 5). Мусор — ScheduleError (в БД его быть не должно)."""
    parts = value.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ScheduleError(f"слоты: не разобрать время {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        raise ScheduleError(f"слоты: недопустимое время {value!r}")
    return hour, minute


def slot_datetime(weekday: int, time_str: str, on_date: datetime) -> datetime:
    """Момент слота на неделе, содержащей `on_date`.

    `weekday` — 0..6 (0 = понедельник), как `datetime.weekday()`. Берётся
    ближайшая дата вперёд в пределах недели: если день недели в `on_date` не
    совпадает, сдвиг идёт вперёд (0..6 дней).
    """
    hour, minute = _parse_hhmm(time_str)
    delta = (weekday - on_date.weekday()) % 7
    target = on_date + timedelta(days=delta)
    return target.replace(hour=hour, minute=minute, second=0, microsecond=0)


def slot_datetime_around(weekday: int, time_str: str, on_date: datetime) -> datetime:
    """Момент слота в ближайшую к `on_date` дату — назад или вперёд.

    Нужен для слота, перешагнувшего полночь: `пт 23:40`, проверяемый в
    субботу 00:10, лежит на день *раньше* `on_date`. `slot_datetime` всегда
    сдвигает вперёд и такой слот не находит.
    """
    hour, minute = _parse_hhmm(time_str)
    forward = (weekday - on_date.weekday()) % 7
    backward = (on_date.weekday() - weekday) % 7
    delta = forward if forward <= backward else -backward
    target = on_date + timedelta(days=delta)
    return target.replace(hour=hour, minute=minute, second=0, microsecond=0)


def due_slot(
    now: datetime,
    slots: list[tuple[int, int, str]],
    config: SlotConfig,
    done: set[str] | None = None,
) -> SlotOccurrence | None:
    """Какой слот наступил и ещё не отработан.

    `slots` — (id, weekday, time). `done` — множество `slot_at`, уже
    отработанных (из `runs`). Возвращает самый ранний наступивший и
    неотработанный слот, либо None.

    Слот «наступил», если now попадает в [slot, slot + catchup_minutes).
    Опоздание больше catchup — слот не догоняется: после суток простоя бот
    не должен вываливать пачку постов. Такие слоты планировщик помечает
    skipped отдельно (`missed_slots`).
    """
    local = config.local_now(now)
    done = done or set()
    candidates: list[SlotOccurrence] = []

    for slot_id, weekday, time_str in slots:
        # Слот 23:40 в пятницу с окном catchup 30 мин попадает в субботу 00:10,
        # поэтому смотрим и вчерашнюю дату. `slot_datetime_around` берёт
        # ближайшую дату в обе стороны — иначе «вчерашний» слот не находится.
        for day_offset in (0, -1):
            day = local + timedelta(days=day_offset)
            slot_moment = slot_datetime_around(weekday, time_str, day)
            window_end = slot_moment + timedelta(minutes=config.catchup_minutes)
            if slot_moment <= local < window_end:
                occurrence = SlotOccurrence(
                    slot_id=slot_id,
                    weekday=weekday,
                    time=time_str,
                    date=slot_moment.strftime("%Y-%m-%d"),
                    slot_at=slot_moment.strftime("%Y-%m-%dT%H:%M"),
                )
                if occurrence.slot_at in done:
                    continue
                candidates.append(occurrence)

    if not candidates:
        return None
    # Раньше всех — тот, что наступил первым. При равном slot_at (два слота
    # в одну минуту невозможны по UNIQUE, но БД может быть правлена руками)
    # решает id: стабильный порядок вместо случайного.
    candidates.sort(key=lambda c: (c.slot_at, c.slot_id))
    return candidates[0]


def missed_slots(
    now: datetime,
    slots: list[tuple[int, int, str]],
    config: SlotConfig,
    done: set[str] | None = None,
    lookback_hours: int = 24,
) -> list[SlotOccurrence]:
    """Слоты, которые прошли и уже не догнать (опоздание > catchup).

    Нужны, чтобы serve пометил их `skipped` в `runs` и не пытался снова.
    Окно просмотра — `lookback_hours` назад: за сутки простоя список
    ограничен, а не растёт от начала времён.
    """
    local = config.local_now(now)
    done = done or set()
    oldest = local - timedelta(hours=lookback_hours)
    missed: list[SlotOccurrence] = []

    seen: set[str] = set()
    weeks_back = lookback_hours // (7 * 24) + 1
    for slot_id, weekday, time_str in slots:
        # `slot_datetime` сам сдвигает на нужный день недели, поэтому перебор
        # дней вручную давал дубли: один слот находился с разных дат-отправлений.
        # Берём ровно одну дату на каждую неделю окна просмотра.
        for week in range(0, weeks_back + 1):
            anchor = local - timedelta(days=week * 7)
            slot_moment = slot_datetime(weekday, time_str, anchor)
            key = slot_moment.strftime("%Y-%m-%dT%H:%M")
            if key in seen:
                continue
            if slot_moment < oldest or slot_moment >= local:
                continue
            window_end = slot_moment + timedelta(minutes=config.catchup_minutes)
            if local < window_end:
                continue
            seen.add(key)
            occurrence = SlotOccurrence(
                slot_id=slot_id,
                weekday=weekday,
                time=time_str,
                date=slot_moment.strftime("%Y-%m-%d"),
                slot_at=key,
            )
            if occurrence.slot_at not in done:
                missed.append(occurrence)

    missed.sort(key=lambda c: (c.slot_at, c.slot_id))
    return missed


def next_occurrence(
    now: datetime,
    slots: list[tuple[int, int, str]],
    config: SlotConfig,
) -> SlotOccurrence | None:
    """Ближайший слот строго после now (для лога «следующий слот»)."""
    if not slots:
        return None
    local = config.local_now(now)
    best: SlotOccurrence | None = None
    for slot_id, weekday, time_str in slots:
        for day_offset in (0, 1):
            day = local + timedelta(days=day_offset)
            slot_moment = slot_datetime(weekday, time_str, day)
            if slot_moment.weekday() != weekday:
                continue
            if slot_moment <= local:
                continue
            occurrence = SlotOccurrence(
                slot_id=slot_id,
                weekday=weekday,
                time=time_str,
                date=slot_moment.strftime("%Y-%m-%d"),
                slot_at=slot_moment.strftime("%Y-%m-%dT%H:%M"),
            )
            if best is None or occurrence.slot_at < best.slot_at:
                best = occurrence
    return best


def lateness_minutes(occurrence: SlotOccurrence, now: datetime, config: SlotConfig) -> int:
    """Насколько прогон опоздал относительно слота, минут (>= 0).

    `slot_at` хранится без таймзоны (это ключ в `runs`, он не должен зависеть
    от зоны процесса), поэтому перед вычитанием доводим его до aware в зоне
    расписания — иначе TypeError на naive-минус-aware.
    """
    local = config.local_now(now)
    slot_moment = occurrence.when
    if slot_moment.tzinfo is None:
        slot_moment = slot_moment.replace(tzinfo=local.tzinfo)
    delta = (local - slot_moment).total_seconds() / 60.0
    return max(0, int(delta))


def is_late(occurrence: SlotOccurrence, now: datetime, config: SlotConfig) -> bool:
    """Публикация позже slot + late_grace_minutes — с пометкой опоздания.

    Пост всё равно уходит (он готов и оплачен токенами), но в `runs` пишется
    причина и строка в дневном итоге. Иначе систематическое торможение LLM
    выглядит как «слоты сами съехали».
    """
    return lateness_minutes(occurrence, now, config) > config.late_grace_minutes


def describe_slots(slots: list[tuple[int, int, str]]) -> str:
    """Сетка одной строкой для лога при старте в режиме slots."""
    if not slots:
        return "(слотов нет)"
    names = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
    by_day: dict[int, list[str]] = {}
    for _slot_id, weekday, time_str in slots:
        by_day.setdefault(weekday, []).append(time_str)
    parts = []
    for day in range(7):
        stamps = sorted(by_day.get(day, []))
        parts.append(f"{names[day]} {'/'.join(stamps) if stamps else '—'}")
    return ", ".join(parts)
