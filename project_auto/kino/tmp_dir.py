"""Единый временный каталог внутри периметра.

Временные файлы пишутся только сюда — не в /tmp (периметр /workspace).
"""
from __future__ import annotations

from pathlib import Path

TMP_DIR = Path(__file__).resolve().parents[1] / ".tmp"


def ensure_tmp_dir() -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    return TMP_DIR