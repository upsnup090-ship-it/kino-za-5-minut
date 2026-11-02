"""Пользовательский бэкап — лёгкий формат (CSV, TXT).

Состав:
  - списки чатов, каналов, юзеров (из bindings)
  - последние 5 постов
  - метрика (кол-во постов, просмотры, репосты)
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_ruch.config import RuchConfig
from project_ruch.ruch_db import RuchDb, RuchDbError

logger = logging.getLogger(__name__)


def _bindings_to_csv(bindings: list[dict[str, Any]]) -> str:
    """Формирует CSV из списка привязок."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["chat_id", "type", "role", "added_at"])
    for b in bindings:
        writer.writerow([b.get("chat_id"), b.get("type"), b.get("role"), b.get("added_at")])
    return output.getvalue()


def _bindings_to_txt(bindings: list[dict[str, Any]]) -> str:
    """Формирует TXT из списка привязок."""
    lines = ["=== Привязки (чаты/каналы/лички) ===", ""]
    for b in bindings:
        lines.append(f"  ID: {b.get('chat_id')} | Тип: {b.get('type')} | Роль: {b.get('role')}")
    lines.append("")
    return "\n".join(lines)


def _posts_to_csv(posts: list[dict[str, Any]]) -> str:
    """Последние посты в CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "channel_chat_id", "text", "media_type", "status", "created_at", "sent_at"])
    for p in posts:
        text_preview = (p.get("text") or "")[:100].replace("\n", " ")
        writer.writerow([
            p.get("id"),
            p.get("channel_chat_id"),
            text_preview,
            p.get("media_type"),
            p.get("status"),
            p.get("created_at"),
            p.get("sent_at"),
        ])
    return output.getvalue()


def _posts_to_txt(posts: list[dict[str, Any]]) -> str:
    """Последние посты в TXT."""
    lines = ["=== Последние 5 постов ===", ""]
    for p in posts:
        lines.append(f"  [#{p.get('id')}] Канал: {p.get('channel_chat_id')}")
        lines.append(f"  Статус: {p.get('status')} | Отправлен: {p.get('sent_at', '—')}")
        text_preview = (p.get("text") or "")[:200]
        lines.append(f"  Текст: {text_preview}")
        lines.append("")
    return "\n".join(lines)


def _stats_to_txt(stats: dict[str, Any]) -> str:
    """Метрика в TXT."""
    lines = ["=== Метрика ===", ""]
    lines.append(f"  Всего постов: {stats.get('total_posts', 0)}")
    lines.append(f"  Всего просмотров: {stats.get('total_views', 0)}")
    lines.append(f"  Всего репостов: {stats.get('total_forwards', 0)}")
    lines.append("")
    return "\n".join(lines)


def _stats_to_csv(stats: dict[str, Any]) -> str:
    """Метрика в CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["total_posts", "total_views", "total_forwards"])
    writer.writerow([stats.get("total_posts", 0), stats.get("total_views", 0), stats.get("total_forwards", 0)])
    return output.getvalue()


def generate_user_backup(
    db: RuchDb,
    user_id: int,
    bindings: list[dict[str, Any]],
    fmt: str = "csv",
    backup_dir: Path | None = None,
) -> str | None:
    """Генерирует бэкап пользователя. Возвращает имя файла или None.

    ``backup_dir`` — явный каталог (тесты); по умолчанию из RuchConfig.
    """
    fmt = fmt.lower()
    if fmt not in ("csv", "txt"):
        fmt = "csv"

    if backup_dir is None:
        backup_dir = RuchConfig().user_backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Собираем данные
    posts = db.get_last_posts(user_id, n=5)
    stats = db.get_stats(user_id)

    # Формируем содержимое
    if fmt == "csv":
        content_parts = [
            _bindings_to_csv(bindings),
            "\n",
            _posts_to_csv(posts),
            "\n",
            _stats_to_csv(stats),
        ]
        ext = "csv"
    else:
        content_parts = [
            _bindings_to_txt(bindings),
            "\n",
            _posts_to_txt(posts),
            "\n",
            _stats_to_txt(stats),
        ]
        ext = "txt"

    content = "\n".join(content_parts)

    # Сохраняем файл. Метка времени — с микросекундами: два бэкапа в одну
    # секунду не должны перезаписывать друг друга (иначе clean_old_backups
    # удаляет единственный файл вместе с «старыми» записями меты).
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    filename = f"backup_user_{user_id}_{timestamp}.{ext}"
    filepath = backup_dir / filename
    filepath.write_text(content, encoding="utf-8")

    # Мета в БД
    db.save_backup_meta(user_id, filename, fmt, filepath.stat().st_size)

    # Чистим старые (оставляем 2 последних)
    db.clean_old_backups(user_id, keep=2, backup_dir=backup_dir)

    return filename
