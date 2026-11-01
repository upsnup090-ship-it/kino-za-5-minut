"""Админка Telegram-бота «Кино за 5 минут» — фасад пакета.

Главная иерархия теперь такая:
`/start` → приветствие → `Автопостинг ТГ` / `О боте` / `Бэкап+логи`.

Пакет разбит по слоям, а не по типам:
- `common` — `Reply`, кнопка, константы;
- `screens` — чистые функции «состояние → Reply»;
- `validation` — проверка ввода;
- `commands` — текстовые команды;
- `callbacks` — `callback_data` → экран;
- `navigation` — переходы `nav:*` и пауза публикаций;
- `posting` — прогон по кнопке;
- `transport` — доступ, polling и отправка в Telegram.

Импорт `kino.admin` работает как раньше: фасад реэкспортирует всё, что
используется снаружи (тесты, `__main__`).
"""

from __future__ import annotations

from kino.admin.callbacks import handle_callback
from kino.admin.commands import handle_command
from kino.admin.common import (
    CONFLICT_BACKOFF_MAX,
    CONFLICT_BACKOFF_START,
    RUNS_SCREEN_LIMIT,
    SLOTS_PER_DAY_WARN,
    Reply,
    _btn,
)
from kino.admin.navigation import (
    _handle_nav,
    _handle_state,
    screen_reserve,
    screen_today,
)
from kino.admin.posting import handle_post_button
from kino.admin.screens import (
    _format_run_line,
    _last_run_line,
    _reserve_stats,
    _safe_paused,
    _serve_state_line,
    screen_about,
    screen_autoposting,
    screen_backup,
    screen_home,
    screen_preview,
    screen_runs,
    screen_schedule,
    screen_slot,
)
from kino.admin.transport import _dispatch, is_admin, poll_once, run
from kino.admin.validation import (
    Validation,
    check_add,
    day_count,
    validate_time,
    validate_weekday,
    warn_slots_per_day,
)

__all__ = [
    # константы
    "CONFLICT_BACKOFF_MAX",
    "CONFLICT_BACKOFF_START",
    "RUNS_SCREEN_LIMIT",
    "SLOTS_PER_DAY_WARN",
    # данные
    "Reply",
    "Validation",
    "_btn",
    # экраны
    "screen_home",
    "screen_schedule",
    "screen_slot",
    "screen_preview",
    "screen_runs",
    "screen_reserve",
    "screen_today",
    # валидация
    "validate_time",
    "validate_weekday",
    "check_add",
    "day_count",
    "warn_slots_per_day",
    # входы
    "handle_command",
    "handle_callback",
    "handle_post_button",
    # транспорт
    "is_admin",
    "poll_once",
    "run",
]