"""Конфигурация модуля RootA."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BACKUP_DIR = ROOT / "_work" / "backups"


@dataclass
class RootAConfig:
    """Настройки RootA."""
    backup_dir: Path = BACKUP_DIR
    backup_max_files: int = 2
    # Исключения бэкапа (паттерны)
    backup_exclude: tuple[str, ...] = (
        ".git/",
        "venv/",
        "_work/backups/",
        "*.db",
        "*.log",
        "*.cache",
    )
