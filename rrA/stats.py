"""Статистика для owner — полная картина."""

from __future__ import annotations

import logging
from typing import Any

from pro_code.clients_db import ClientsDb
from project_ruch.ruch_db import RuchDb
from project_ruch.stats import owner_stats as ruch_owner_stats

logger = logging.getLogger(__name__)


def full_owner_report(
    clients_db: ClientsDb,
    ruch_db: RuchDb,
) -> dict[str, Any]:
    """Полный отчёт для owner: клиенты, боты, привязки, активность."""
    clients = clients_db.list_clients()

    # Собираем привязки по всем клиентам
    bindings_per_user: dict[int, list[dict[str, Any]]] = {}
    for c in clients:
        uid = c["user_id"]
        bindings_per_user[uid] = clients_db.get_bindings(uid)

    # Статистика из ручного модуля
    ruch_stats = ruch_owner_stats(ruch_db, clients, bindings_per_user)

    # Дополнительно: какие клиенты активны/неактивны
    active_count = sum(1 for c in clients if c.get("status") == "active")
    inactive_count = sum(1 for c in clients if c.get("status") == "inactive")

    return {
        **ruch_stats,
        "active_clients": active_count,
        "inactive_clients": inactive_count,
        "clients_list": [
            {
                "user_id": c["user_id"],
                "plan": c.get("plan", "free"),
                "status": c.get("status", "active"),
                "bindings_count": len(bindings_per_user.get(c["user_id"], [])),
            }
            for c in clients
        ],
    }
