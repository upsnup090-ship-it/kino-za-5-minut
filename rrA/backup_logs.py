"""Главный бэкап+логи — только для owner.

Бэкап: архивирует /workspace (с исключениями), хранит 2 последних.
Логи: просмотр и экспорт _agent_log.md и project_ruch/logs/*.log.
"""

from __future__ import annotations

import logging
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rrA.config import RootAConfig

logger = logging.getLogger(__name__)


def make_backup(dry_run: bool = False) -> dict[str, Any]:
    """Создаёт бэкап /workspace (с исключениями). Хранит 2 последних.

    Возвращает dict с результатом.
    """
    cfg = RootAConfig()
    backup_dir = cfg.backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Удаляем старые бэкапы (оставляем 2 последних)
    existing = sorted(backup_dir.glob("backup_*.tar.gz"), reverse=True)
    for old in existing[cfg.backup_max_files - 1:]:
        old.unlink()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_name = f"backup_{timestamp}.tar.gz"
    archive_path = backup_dir / archive_name

    if dry_run:
        return {"ok": True, "filename": archive_name, "dry_run": True}

    # Сбор файлов для бэкапа
    workspace = Path("/workspace")
    exclude_patterns = cfg.backup_exclude

    def _should_exclude(path: Path) -> bool:
        rel = path.relative_to(workspace).as_posix()
        for pat in exclude_patterns:
            if pat.endswith("/"):
                # Каталог
                if rel.startswith(pat) or rel + "/" == pat:
                    return True
            elif pat.startswith("*."):
                # Расширение
                if rel.endswith(pat[1:]):
                    return True
            elif rel == pat:
                return True
        return False

    try:
        with tarfile.open(archive_path, "w:gz") as tar:
            for entry in workspace.iterdir():
                if _should_exclude(entry):
                    continue
                if entry.name.startswith(".") and entry.name not in (".env.example",):
                    continue
                tar.add(entry, arcname=entry.name)
    except Exception as exc:
        logger.error("ошибка создания бэкапа: %s", exc)
        return {"ok": False, "error": str(exc)}

    size_bytes = archive_path.stat().st_size

    # Чистим старые (если после добавления стало >2)
    existing_after = sorted(backup_dir.glob("backup_*.tar.gz"), reverse=True)
    for old in existing_after[cfg.backup_max_files:]:
        old.unlink()

    return {
        "ok": True,
        "filename": archive_name,
        "size_bytes": size_bytes,
        "path": str(archive_path),
    }


def list_backups() -> list[dict[str, Any]]:
    """Список существующих бэкапов."""
    cfg = RootAConfig()
    backups = sorted(cfg.backup_dir.glob("backup_*.tar.gz"), reverse=True)
    return [
        {
            "filename": b.name,
            "size_bytes": b.stat().st_size,
            "created_at": datetime.fromtimestamp(b.stat().st_mtime, tz=timezone.utc).isoformat(),
        }
        for b in backups
    ]


def get_logs_paths() -> dict[str, list[Path]]:
    """Пути к логам: _agent_log.md и project_ruch/logs/."""
    workspace = Path("/workspace")
    paths = {
        "agent_log": [workspace / "_agent_log.md"],
        "ruch_logs": sorted((workspace / "project_ruch" / "logs").glob("*.log")) if (workspace / "project_ruch" / "logs").exists() else [],
    }
    return paths


def export_logs(log_type: str = "all") -> dict[str, Any]:
    """Экспорт логов: 'agent' / 'ruch' / 'all' — возвращает содержимое."""
    result: dict[str, Any] = {"ok": True, "content": {}}
    paths = get_logs_paths()

    if log_type in ("agent", "all"):
        for p in paths["agent_log"]:
            if p.exists():
                result["content"]["_agent_log.md"] = p.read_text(encoding="utf-8", errors="replace")

    if log_type in ("ruch", "all"):
        for p in paths["ruch_logs"]:
            if p.exists():
                result["content"][p.name] = p.read_text(encoding="utf-8", errors="replace")

    return result
