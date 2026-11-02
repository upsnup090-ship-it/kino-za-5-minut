"""Конфигурация модуля ручного направления."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class RuchConfig:
    """Настройки модуля Ruch."""
    db_path: Path = ROOT / "data" / "ruch.db"
    # Повторяющиеся сообщения
    max_repeating_per_client: int = 50
    # Бэкап
    user_backup_max_files: int = 2
    user_backup_dir: Path = ROOT / "data" / "user_backups"
