"""Конфигурация модуля резерва."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ReserveConfig:
    """Настройки резерва."""
    max_items_per_client: int = 10
    ttl_hours: int = 48
