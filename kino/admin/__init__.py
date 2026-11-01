"""Админка расписания в личке бота — фасад пакета.

Две поверхности, обе обязательны:

- **кнопки** — основной UI: экран S0 (compact/full), расписание, карточка
  слота, превью, прогоны, резерв, итог за вчера;
- **команды** — быстрый путь: `/schedule`, `/slot add|del|on|off|clear`,
  `/next`. Команда отвечает тем же экраном с кнопками, а не отдельным
  текстом: два пути к одному состоянию.

Доступ только по `ADMIN_CHAT_ID`, проверка на каждом апдейте; чужой чат —
отказ и запись в лог.

Пакет разбит по слоям, а не по типам:

- `common` — `Reply`, кнопка, константы (без зависимостей от проекта);
- `screens` — чистые функции «состояние → Reply», без сети;
- `validation` — проверка ввода, понятные ошибки по-русски;
- `commands` — текстовые команды, отвечают теми же экранами;
- `callbacks` — `callback_data` → экран;
- `navigation` — переходы `nav:*` и пауза публикаций;
- `posting` — прогон по кнопке в отдельном потоке;
- `transport` — доступ, опрос апдейтов, цикл 24/7.

Почему пакет, а не один файл (задача #68): 977 строк в одном модуле
затрудняют поиск и провоцируют циклические импорты, когда экран нужен
и команде, и колбэку. Здесь зависимости идут в одну сторону:
`common → screens → commands/callbacks/navigation → transport`.

Импорт `kino.admin` работает как раньше: фасад реэкспортирует всё, что
использовалось снаружи (тесты, `__main__`).
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