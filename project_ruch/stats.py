"""Статистика — сбор и агрегация показателей.

Для owner: статистика по всем параметрам, состав клиентов,
количество подключённых ботов, где что используется.
Для user («стандарт»): по его привязанному боту.
"""

from __future__ import annotations

import logging
from typing import Any

from project_ruch.ruch_db import RuchDb

logger = logging.getLogger(__name__)


def user_stats(db: RuchDb, user_id: int) -> dict[str, Any]:
    """Статистика пользователя по его боту."""
    return db.get_stats(user_id)


def owner_stats(
    db: RuchDb,
    clients_data: list[dict[str, Any]],
    bindings_per_user: dict[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Полная статистика для owner.

    clients_data — список клиентов (из clients.db)
    bindings_per_user — привязки чатов/каналов по user_id
    """
    total_clients = len(clients_data)
    total_bots = total_clients  # по одному боту на клиента
    total_bindings = sum(len(b) for b in bindings_per_user.values())

    # Распределение по типам
    type_dist: dict[str, int] = {}
    for user_bindings in bindings_per_user.values():
        for b in user_bindings:
            t = b.get("type", "unknown")
            type_dist[t] = type_dist.get(t, 0) + 1

    # Распределение по планам
    plan_dist: dict[str, int] = {}
    for c in clients_data:
        p = c.get("plan", "free")
        plan_dist[p] = plan_dist.get(p, 0) + 1

    return {
        "total_clients": total_clients,
        "total_bots": total_bots,
        "total_bindings": total_bindings,
        "by_type": type_dist,
        "by_plan": plan_dist,
    }
