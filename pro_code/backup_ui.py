"""Пользовательский бэкап (baseline §8) — отправка файла клиенту.

Команда `/backup` в боте клиента: генерирует лёгкий бэкап
(привязки, 5 последних постов, метрика) в CSV или TXT, отправляет
файлом через sendDocument. Хранятся 2 последних (clean_old_backups).
"""

from __future__ import annotations

import logging
from pathlib import Path

from pro_code.clients_db import ClientsDb
from pro_code.tg_api import TgError, send_document, send_message
from project_ruch.backup import generate_user_backup
from project_ruch.config import RuchConfig
from project_ruch.ruch_db import RuchDb

logger = logging.getLogger(__name__)

HELP = (
    "💾 **Бэкап (лёгкий)**\n\n"
    "Собираю твои данные в файл:\n"
    "• списки чатов/каналов;\n"
    "• последние 5 постов;\n"
    "• метрика.\n\n"
    "Формат: `/backup csv` или `/backup txt` (по умолчанию CSV).\n"
    "Хранятся 2 последних бэкапа."
)


def send_user_backup(token: str, chat_id: int, user_id: int, fmt: str = "csv") -> None:
    clients_db = ClientsDb()
    ruch_db = RuchDb()
    try:
        bindings = ruch_db.list_bindings(user_id)
        filename = generate_user_backup(ruch_db, user_id, bindings, fmt=fmt)
    finally:
        ruch_db.close()

    if not filename:
        try:
            send_message(token, chat_id, "❌ Не удалось создать бэкап. Попробуй ещё раз.")
        except TgError:
            pass
        return

    path = RuchConfig().user_backup_dir / filename
    try:
        send_document(token, chat_id, str(path), caption=f"💾 Бэкап `{filename}`")
        send_message(
            token, chat_id,
            f"✅ Бэкап готов: `{filename}`\nХранятся 2 последних.",
        )
    except TgError as exc:
        logger.warning("не удалось отправить бэкап %s: %s", filename, exc)
        try:
            send_message(token, chat_id, "❌ Бэкап создан, но не отправлен. Попробуй позже.")
        except TgError:
            pass


def handle(
    token: str, chat_id: int, user_id: int, text: str,
) -> bool:
    """Команды бэкапа. True — поглощено."""
    cmd = text.strip().lower()
    if cmd in ("/backup", "💾 бэкап", "бэкап", "backup", "💾"):
        send_user_backup(token, chat_id, user_id, "csv")
        return True
    if cmd == "/backup csv":
        send_user_backup(token, chat_id, user_id, "csv")
        return True
    if cmd == "/backup txt":
        send_user_backup(token, chat_id, user_id, "txt")
        return True
    if cmd.startswith("/backup"):
        try:
            send_message(token, chat_id, HELP)
        except TgError:
            pass
        return True
    return False
