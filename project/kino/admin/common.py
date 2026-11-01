"""Общее для всех модулей админки: `Reply`, кнопка, константы.

`Reply` — чистые данные: текст и инлайн-клавиатура. Ни сети, ни Telegram.
Именно поэтому экраны проверяются тестами без моков транспорта.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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