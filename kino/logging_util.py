"""Логи в файл с ротацией и автоочисткой.

Два независимых механизма, потому что одного мало:

- **Ротация по размеру** (`RotatingFileHandler`) — активный файл ограничен,
  старые версии сдвигаются и удаляются по счётчику. Работает всегда, без крона.
- **Очистка по возрасту** (`purge_old_logs`) — при старте процесса и раз в сутки
  в цикле `serve` удаляются файлы логов старше `retention_days`, а раздутый
  каталог ужимается до `max_total_bytes`.

Возраст ловит «редко, но долго» (файл свежий по размеру, старый по времени).
Размер ловит «много и быстро» (всплеск ошибок за час). Вместе — инвариант:
каталог логов не превышает лимит и не хранит записи старше срока.
"""

from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    *,
    file: Path | None = None,
    level: str = "INFO",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
    console: bool = True,
) -> None:
    """Настраивает логи: файл с ротацией + (опц.) stdout.

    Файл: `file` (обычно logs/kino.log), ротация по размеру `max_bytes`,
    хранится `backup_count` версий. stdout дублируется, если `console=True`
    (systemd/journald и ручной запуск должны видеть то же).

    Уровень — из `level` (по умолчанию INFO). Не бросает исключение при
    недоступном каталоге: логирование не должно ронять приложение.
    """
    level_num = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    # Сбрасываем существующие хендлеры: иначе при повторном вызове (в тестах,
    # при нескольких serve) записи дублируются.
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001 — закрытие хендлера не должно мешать
            pass

    root.setLevel(level_num)

    handlers: list[logging.Handler] = []

    if file is not None:
        try:
            file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                str(file),
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setLevel(level_num)
            file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
            handlers.append(file_handler)
        except OSError as exc:
            logger.warning("не удалось открыть файл логов %s: %s", file, exc)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level_num)
        console_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        handlers.append(console_handler)

    for handler in handlers:
        root.addHandler(handler)

    # Промежуточные логгеры пакета наследуют уровень от root; явно не
    # приглушаем чужие шумные (requests) — они уже на уровне INFO.
    if level_num <= logging.DEBUG:
        logging.getLogger("kino").setLevel(logging.DEBUG)


def purge_old_logs(
    log_dir: Path,
    *,
    retention_days: int = 14,
    max_total_bytes: int = 100 * 1024 * 1024,
    keep_names: tuple[str, ...] = ("kino.log",),
) -> list[Path]:
    """Удаляет старые логи. Возвращает список удалённых файлов.

    Два правила, применяются по порядку:

    1. По возрасту: файлы (включая ротированные `kino.log.1`) старше
       `retention_days` удаляются.
    2. По суммарному размеру: если после шага 1 каталог всё ещё больше
       `max_total_bytes`, удаляются самые старые, пока не уложимся.

    Активный файл (`kino.log`) не удаляется никогда — его держит открытым
    обработчик; ротированные версии удалять можно. Каждое удаление пишется в
    лог (сколько файлов, сколько освободилось). Ошибки не роняют процесс.
    """
    removed: list[Path] = []
    removed_bytes = 0
    if not log_dir.exists():
        return removed

    now = time.time()
    retention_seconds = retention_days * 86400

    def _candidate(path: Path) -> bool:
        """Все .log-файлы, кроме активного (его держит открытым обработчик)."""
        if path.name in keep_names:
            return False
        return path.suffix == ".log" or ".log." in path.name

    files = [p for p in log_dir.iterdir() if p.is_file() and _candidate(p)]

    # Шаг 1: по возрасту.
    for path in list(files):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if now - mtime > retention_seconds:
            size = _file_size(path)
            if _remove_file(path, removed):
                removed_bytes += size

    # Шаг 2: по суммарному размеру (после шага 1).
    current_files = [p for p in log_dir.iterdir() if p.is_file() and _candidate(p)]
    total = sum(_file_size(p) for p in current_files)
    if total > max_total_bytes:
        # Самые старые первыми — по mtime.
        current_files.sort(key=lambda p: p.stat().st_mtime)
        for path in current_files:
            if total <= max_total_bytes:
                break
            size = _file_size(path)
            if _remove_file(path, removed):
                removed_bytes += size
                total -= size

    if removed:
        logger.info(
            "автоочистка логов: удалено %d файлов, освобождено ~%.1f КБ",
            len(removed),
            removed_bytes / 1024,
        )
    return removed


def _remove_file(path: Path, removed: list[Path]) -> bool:
    try:
        path.unlink()
    except OSError:
        logger.warning("не удалось удалить лог %s", path)
        return False
    removed.append(path)
    logger.debug("удалён лог %s", path.name)
    return True


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0