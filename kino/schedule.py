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