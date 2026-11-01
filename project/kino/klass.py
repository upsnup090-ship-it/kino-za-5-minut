"""Классификатор файлов по пути.

Заменяет определение "оригинала через намерение" на определение через путь.

Путь нормализуется строково (normpath), symlink'и НЕ раскрываются:
`venv/bin/python` остаётся `.../project/venv/bin/python` и классифицируется
как EXCLUDED, а не уезжает resolve()'ом в /usr/lib.

Классы:
    ORIG      — всё остальное (код, конфиги, документация)
    DERIV     — производится кодом, не state и не secret (логи, кэш)
    STATE     — состояние бота, runtime, не копируется целиком
    SECRET    — секреты, в чат не выводятся никогда
    EXCLUDED  — целиком исключены из понятия "оригинал"
    UNKNOWN   — путь вне project_root
"""
from __future__ import annotations

import enum
import fnmatch
import os
from pathlib import Path


class FileClass(enum.Enum):
    ORIG = "ORIG"
    DERIV = "DERIV"
    STATE = "STATE"
    SECRET = "SECRET"
    EXCLUDED = "EXCLUDED"
    UNKNOWN = "UNKNOWN"


# Компоненты каталогов, исключающие всё внутри — на ЛЮБОЙ глубине
# (mcp/venv/, kino/__pycache__/ и т.п.). Сравниваются по элементам пути
# без последнего (имя файла). logs/ и data/feed_cache/ сюда НЕ входят:
# это DERIV (производные), см. DERIV_PATTERNS.
EXCLUDED_COMPONENTS = frozenset({
    ".git",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
})

# DERIV: производится кодом, не state, не secret.
DERIV_PATTERNS = (
    "logs/*",
    "data/feed_cache/*",
    "__pycache__/*",
    ".mypy_cache/*",
    ".pytest_cache/*",
)

# STATE: изменяется runtime, не редактируется вручную.
STATE_PATTERNS = (
    "data/*.db",
    "data/*.db-wal",
    "data/*.db-shm",
    "data/state.json",
)

# SECRET: в чат не выводится, никогда не копируется.
SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "data/graph_token.txt",
)


def _match(rel: str, pattern: str) -> bool:
    if fnmatch.fnmatch(rel, pattern):
        return True
    if fnmatch.fnmatch(rel, pattern + "/*"):
        return True
    # сам каталог паттерна ("data/feed_cache" по паттерну "data/feed_cache/*")
    return rel == pattern.rstrip("/*")


#: Корень проекта по умолчанию — каталог пакета kino на одну ступень выше
#: (кросс-платформенно: работает и в песочнице /workspace/project, и на
#: сервере /opt/kino/app).
_DEFAULT_ROOT = str(Path(__file__).resolve().parents[1])


def classify(
    path: str | Path,
    *,
    project_root: str | Path = "",
) -> FileClass:
    root = os.path.normpath(project_root or _DEFAULT_ROOT)
    raw = str(path)
    if not os.path.isabs(raw):
        raw = os.path.join(root, raw)
    target = os.path.normpath(raw)
    if target == root:
        return FileClass.UNKNOWN
    prefix = root + os.sep
    if not target.startswith(prefix):
        return FileClass.UNKNOWN
    rel = target[len(prefix):]

    parts = Path(rel).parts
    # EXCLUDED: компонент-каталог на любой глубине (кроме последнего = имя файла)
    for comp in parts[:-1]:
        if comp in EXCLUDED_COMPONENTS:
            return FileClass.EXCLUDED

    for pat in SECRET_PATTERNS:
        if _match(rel, pat):
            return FileClass.SECRET

    for pat in STATE_PATTERNS:
        if _match(rel, pat):
            return FileClass.STATE

    for pat in DERIV_PATTERNS:
        if _match(rel, pat):
            return FileClass.DERIV

    return FileClass.ORIG


def is_original(
    path: str | Path,
    *,
    project_root: str | Path = "",
) -> bool:
    return classify(path, project_root=project_root) == FileClass.ORIG