"""Управление клиентами: добавление, список, план, статус, привязки."""

from __future__ import annotations

import logging
from typing import Any

from pro_code.clients_db import ClientsDb
from pro_code.tg_api import get_me, TgError

logger = logging.getLogger(__name__)


def list_clients(db: ClientsDb) -> list[dict[str, Any]]:
    """Полный список клиентов."""
    return db.list_clients()


def get_client(db: ClientsDb, user_id: int) -> dict[str, Any] | None:
    """Информация о клиенте."""
    return db.get_client(user_id)


def add_client(
    db: ClientsDb,
    user_id: int,
    plan: str = "free",
    status: str = "active",
) -> dict[str, Any]:
    """Добавить/обновить клиента."""
    db.upsert_client(user_id, plan=plan, status=status)
    return {"user_id": user_id, "plan": plan, "status": status}


def set_plan(db: ClientsDb, user_id: int, plan: str) -> bool:
    """Сменить план клиента (free/auto)."""
    if plan not in ("free", "auto"):
        return False
    db.upsert_client(user_id, plan=plan)
    return True


def set_status(db: ClientsDb, user_id: int, status: str) -> bool:
    """Сменить статус (active/inactive)."""
    if status not in ("active", "inactive"):
        return False
    db.set_status(user_id, status)
    return True


def verify_client_token(token: str) -> dict[str, Any] | None:
    """Проверить токен через getMe. Вернуть информацию о боте или None."""
    try:
        info = get_me(token)
        return {
            "id": info.get("id"),
            "username": info.get("username"),
            "first_name": info.get("first_name"),
        }
    except TgError:
        return None


def get_bindings(db: ClientsDb, user_id: int) -> list[dict[str, Any]]:
    """Привязки чатов/каналов клиента."""
    return db.get_bindings(user_id)
