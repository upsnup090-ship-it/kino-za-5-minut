"""Бюджет времени на прогон: мягкое завершение вместо зависания.

Один пост не должен съедать весь бюджет времени цикла 24/7: зависший источник
или медленный LLM сдвигает расписание. `Deadline` даёт шагам понять, что пора
закругляться, но не убивает процесс на середине публикации (это было бы опасно:
пост ушёл бы в канал, а state не записался — следующий прогон создал бы дубль).
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class Deadline:
    """Отсчитывает бюджет и умеет сказать, сколько осталось.

    Использует `time.monotonic()` — не зависит от смены системного времени
    (перевод часов при NTP не вызовет ложного истечения).

    `total_seconds=None` или `<= 0` означает «не ограничено»: дедлайн всегда
    даёт зелёный свет. Это режим обратной совместимости, когда бюджет не задан.
    """

    def __init__(self, total_seconds: float | None):
        if total_seconds is None or total_seconds <= 0:
            self.total = float("inf")
        else:
            self.total = float(total_seconds)
        self.started = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def remaining(self) -> float:
        return max(0.0, self.total - self.elapsed)

    @property
    def expired(self) -> bool:
        return self.remaining <= 0

    def slice(self, want: float) -> float:
        """Сколько можно потратить на шаг: min(want, remaining).

        Если осталось меньше, чем хотелось, — вернёт остаток. Если дедлайн не
        задан (`inf`) — вернёт `want` как есть.
        """
        if self.total == float("inf"):
            return want
        return min(want, self.remaining)

    def __repr__(self) -> str:
        return f"Deadline(remaining={self.remaining:.1f}s, total={self.total!r})"


def check(deadline: Deadline | None, *, step: str) -> bool:
    """True, если можно продолжать (бюджет не исчерпан).

    Централизует лог: «бюджет исчерпан на шаге X, останавливаемся».
    `None`-дедлайн возвращает True всегда (обратная совместимость).
    """
    if deadline is None:
        return True
    if not deadline.expired:
        return True
    logger.warning("дедлайн прогона исчерпан на шаге %s", step)
    return False