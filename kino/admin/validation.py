"""Валидация ввода: время, день недели, дубли слотов.

Каждая функция возвращает `Validation` — значение или понятную ошибку
по-русски. Мусор не исключение, а текст, который человек прочитает.
"""

from __future__ import annotations

from dataclasses import dataclass

from kino.config import Config
from kino.schedule_db import (
    WEEKDAY_NAMES,
    ScheduleDb,
    ScheduleDbError,
    normalize_time,
    normalize_weekday,
)

from kino.admin.common import SLOTS_PER_DAY_WARN

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

