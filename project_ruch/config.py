"""Конфигурация модуля ручного направления."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]

#: Зона по умолчанию для расписаний ручного модуля.
DEFAULT_TIMEZONE = "Europe/Moscow"


def _resolve_timezone() -> ZoneInfo:
    """Зона из RUCH_TIMEZONE, иначе Europe/Moscow.

    Неизвестная зона не роняет модуль: пишем предупреждение и берём дефолт.
    Иначе опечатка в .env убила бы весь диспетчер на старте.
    """
    name = os.environ.get("RUCH_TIMEZONE", "").strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        import logging

        logging.getLogger(__name__).warning(
            "неизвестная зона %r, беру %s", name, DEFAULT_TIMEZONE
        )
        return ZoneInfo(DEFAULT_TIMEZONE)


@dataclass
class RuchConfig:
    """Настройки модуля Ruch."""

    db_path: Path = ROOT / "data" / "ruch.db"
    # Повторяющиеся сообщения
    max_repeating_per_client: int = 50
    #: Зона, в которой заданы время и дни повторяющихся сообщений.
    timezone: ZoneInfo = field(default_factory=_resolve_timezone)
    # Бэкап
    user_backup_max_files: int = 2
    user_backup_dir: Path = ROOT / "data" / "user_backups"