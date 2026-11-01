"""Классификатор файлов по пути — единственный источник правды о классах.

Заменяет определение "оригинала через намерение" на определение через путь.

Путь нормализуется строково (normpath), symlink'и НЕ раскрываются:
`venv/bin/python` остаётся `.../project/venv/bin/python` и классифицируется
как EXCLUDED, а не уезжает resolve()'ом в /usr/lib.

Два уровня:

1. **Проект** (`kino/../`, т.е. `project/` в песочнице, `/opt/kino/app`
   на сервере) — таблицы паттернов ниже.
2. **Периметр** (git-корень, если он выше проекта; иначе совпадает с
   проектом) — верхний уровень: `AGENT.md`, `_work/`, `_agent_log.md`.

Классы:
    ORIG      — код, конфиги, документация; единственный защищаемый класс
    DERIV     — производится кодом, не state и не secret (логи, кэш, .tmp)
    STATE     — состояние бота, runtime, не копируется целиком
    SECRET    — секреты, в чат не выводятся никогда
    LOG       — журнал агента: только дополнение, не перезапись
    EXCLUDED  — целиком исключены из понятия "оригинал" (venv, mcp, кэши)
    UNKNOWN   — путь не описан законом: спросить оператора
"""
from __future__ import annotations

import enum
import fnmatch
import os
import sys
from pathlib import Path


class FileClass(enum.Enum):
    ORIG = "ORIG"
    DERIV = "DERIV"
    STATE = "STATE"
    SECRET = "SECRET"
    LOG = "LOG"
    EXCLUDED = "EXCLUDED"
    UNKNOWN = "UNKNOWN"


# Компоненты каталогов, исключающие всё внутри — на ЛЮБОЙ глубине
# (mcp/venv/, kino/__pycache__/ и т.п.). Сравниваются по элементам пути
# без последнего (имя файла). logs/ и data/feed_cache/ сюда НЕ входят:
# это DERIV (производные), см. DERIV_PATTERNS.
#
# mcp/ — эксперименты вне репозитория (решение оператора 2026-11-01):
# в git не идут, оригиналом не считаются.
EXCLUDED_COMPONENTS = frozenset({
    ".git",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "mcp",
})

# DERIV: производится кодом, не state, не secret.
DERIV_PATTERNS = (
    "logs/*",
    "data/feed_cache/*",
    ".tmp/*",
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

#: Верхний уровень периметра: относительный путь → класс.
#: Всё, чего здесь нет, — UNKNOWN (закон молчит, спросить оператора).
PERIMETER_FILES = {
    "AGENT.md": FileClass.ORIG,
    ".gitignore": FileClass.ORIG,
    "_agent_log.md": FileClass.LOG,
}
#: Артефакты-архивы в корне периметра: не код, в git не идут (решение
#: оператора 2026-11-01 — не трогать, но и не считать «неизвестными»).
PERIMETER_PATTERNS = (
    ("*.zip", FileClass.EXCLUDED),
    ("*.patch", FileClass.EXCLUDED),
    ("*.tar.gz", FileClass.EXCLUDED),
)
PERIMETER_DIRS = {
    "_work": FileClass.DERIV,
    "venv": FileClass.EXCLUDED,
    ".git": FileClass.EXCLUDED,
    "__pycache__": FileClass.EXCLUDED,
}


def _match(rel: str, pattern: str) -> bool:
    if fnmatch.fnmatch(rel, pattern):
        return True
    if fnmatch.fnmatch(rel, pattern + "/*"):
        return True
    # сам каталог паттерна ("data/feed_cache" по паттерну "data/feed_cache/*")
    return rel == pattern.rstrip("/*")


#: Корень проекта — каталог пакета kino на одну ступень выше
#: (кросс-платформенно: работает и в песочнице /workspace/project, и на
#: сервере /opt/kino/app).
_DEFAULT_ROOT = str(Path(__file__).resolve().parents[1])


def _perimeter_root() -> str:
    """Корень периметра: git-корень выше проекта, иначе сам проект.

    В песочнице `.git` лежит в `/workspace`, а код — в `/workspace/project`:
    без этого `AGENT.md` попадал в UNKNOWN, и главный защищаемый файл
    выпадал из классификации. На сервере (`/opt/kino/app`) `.git` нет —
    периметр совпадает с проектом, поведение прежнее.
    """
    root = Path(_DEFAULT_ROOT)
    for candidate in (root, root.parent):
        if (candidate / ".git").exists():
            return str(candidate)
    return str(root)


def _rel_inside(root: str, path: str | Path) -> str | None:
    """Относительный путь от root, либо None если путь вне root."""
    raw = str(path)
    if not os.path.isabs(raw):
        raw = os.path.join(root, raw)
    target = os.path.normpath(raw)
    if target == root:
        return ""
    prefix = root + os.sep
    if not target.startswith(prefix):
        return None
    return target[len(prefix):]


def _classify_in_project(rel: str) -> FileClass:
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


def _classify_in_perimeter(rel: str) -> FileClass:
    if rel == "":
        return FileClass.UNKNOWN
    top = Path(rel).parts[0]
    if len(Path(rel).parts) == 1:
        if rel in PERIMETER_FILES:
            return PERIMETER_FILES[rel]
        for pat, cls in PERIMETER_PATTERNS:
            if fnmatch.fnmatch(rel, pat):
                return cls
        if top in PERIMETER_DIRS:
            return PERIMETER_DIRS[top]
    if top in PERIMETER_DIRS:
        return PERIMETER_DIRS[top]
    return FileClass.UNKNOWN


def classify(
    path: str | Path,
    *,
    project_root: str | Path = "",
    perimeter_root: str | Path = "",
) -> FileClass:
    """Класс файла по пути.

    project_root — корень кода (по умолчанию `kino/..`).
    perimeter_root — корень периметра (по умолчанию git-корень выше проекта).
    """
    root = os.path.normpath(project_root or _DEFAULT_ROOT)

    rel = _rel_inside(root, path)
    if rel is not None and rel != "":
        return _classify_in_project(rel)

    per = os.path.normpath(perimeter_root or _perimeter_root())
    if per != root:
        rel_per = _rel_inside(per, path)
        if rel_per is not None:
            return _classify_in_perimeter(rel_per)

    return FileClass.UNKNOWN


def is_original(
    path: str | Path,
    *,
    project_root: str | Path = "",
) -> bool:
    return classify(path, project_root=project_root) == FileClass.ORIG


# --- CLI: python -m kino.klass ------------------------------------------------

_EXCLUDED_EXTRA = (".tmp", "mcp")


def _rsync_excludes() -> list[str]:
    """Список --exclude для deploy/update.sh, выведенный из таблиц классов.

    Раньше update.sh держал свою копию списка (K2) и разошёлся с klass.py:
    rsync заливал на сервер `.tmp/` и `mcp/`. Теперь список один.
    """
    dirs = list(EXCLUDED_COMPONENTS)
    dirs += [d for d in _EXCLUDED_EXTRA if d not in dirs]
    dirs += [p.split("/")[0] for p in DERIV_PATTERNS]
    dirs += [p.split("/")[0] for p in STATE_PATTERNS]
    names = [".env"] + [p for p in SECRET_PATTERNS if "/" not in p]

    out: list[str] = []
    for d in dirs:
        if d == ".git":
            continue
        item = d + "/"
        if item not in out:
            out.append(item)
    for n in names:
        if n not in out:
            out.append(n)
    return out


def audit_tree(root: str | Path = "") -> list[str]:
    """Файлы, которые закон не описывает (UNKNOWN) внутри периметра."""
    per = Path(root or _perimeter_root())
    problems: list[str] = []
    for entry in sorted(per.iterdir()):
        if entry.name == "project":
            continue  # проект разбирается своими таблицами
        if entry.is_dir() and not any(p.is_file() for p in entry.rglob("*")):
            continue  # пустой каталог-артефакт: удалять нечего, закон молчит
        cls = classify(entry)
        if cls is FileClass.UNKNOWN:
            problems.append(f"{entry}: UNKNOWN (класс не описан)")
    for path in sorted(Path(_DEFAULT_ROOT).rglob("*.py")):
        if "venv" in path.parts or "__pycache__" in path.parts:
            continue
        cls = classify(path)
        if cls in (FileClass.ORIG, FileClass.EXCLUDED):
            continue
        problems.append(f"{path}: {cls.value} в коде проекта")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Использование: python -m kino.klass "
              "--classify <путь...> | --audit | --rsync-excludes")
        return 2

    if args[0] == "--classify":
        if len(args) < 2:
            print("нужен хотя бы один путь")
            return 2
        bad = 0
        for raw in args[1:]:
            cls = classify(raw)
            print(f"{cls.value:9} {raw}")
            if cls is FileClass.UNKNOWN:
                bad += 1
        return 1 if bad else 0

    if args[0] == "--audit":
        problems = audit_tree()
        if problems:
            print("Нарушения классификации:")
            for p in problems:
                print(" ", p)
            return 1
        print("OK: все пути периметра описаны законом")
        return 0

    if args[0] == "--rsync-excludes":
        for name in _rsync_excludes():
            print(f'  --exclude "{name}" \\')
        return 0

    print(f"неизвестный режим: {args[0]}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
