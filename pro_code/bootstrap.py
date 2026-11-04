"""Подготовка путей импорта: `kino` живёт в подкаталоге ``project/``.

Пакет Kino лежит в ``/workspace/project_auto/kino`` и рассчитан на запуск из
``project/`` (CLI ``python -m kino``, systemd с ``WorkingDirectory``). Точка
входа диспетчера — ``python -m pro_code.main`` из ``/workspace``, и в её
``sys.path`` есть только корень, поэтому ``import kino`` падал с
``ModuleNotFoundError``: автопостинг не поднимался вовсе.

Модуль добавляет каталог проекта в ``sys.path`` один раз и идемпотентно.
Отдельным модулем — чтобы это было видно и тестируемо, а не пряталось
побочным эффектом в ``main``.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Корень репозитория = /workspace (на уровень выше pro_code/).
ROOT = Path(__file__).resolve().parents[1]

#: Каталог, где лежит пакет ``kino``.
PROJECT_DIR = ROOT / "project_auto"


def ensure_kino_importable() -> None:
    """Добавить ``project_auto/`` в ``sys.path``, если его там нет.

    Идемпотентно: повторный вызов ничего не меняет. Вставка в начало, чтобы
    локальный ``kino`` проекта имел приоритет над одноимённой установленной
    библиотекой, если такая когда-нибудь появится.
    """
    target = str(PROJECT_DIR)
    if target not in sys.path:
        sys.path.insert(0, target)
