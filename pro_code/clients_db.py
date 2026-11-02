"""clients.db — таблица clients (без токенов).

Токены — только в .env, не в БД.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "clients.db"


class ClientsDbError(Exception):
    """Ошибка БД клиентов."""


def _ensure_dir() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    user_id     INTEGER PRIMARY KEY,
    plan        TEXT    NOT NULL DEFAULT 'free'
                        CHECK (plan IN ('free', 'auto')),
    status      TEXT    NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'inactive')),
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS bindings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES clients(user_id),
    chat_id     INTEGER NOT NULL,
    type        TEXT    NOT NULL CHECK (type IN ('chat', 'channel', 'private')),
    role        TEXT    DEFAULT 'member',
    added_at    TEXT    NOT NULL,
    UNIQUE(user_id, chat_id)
);
"""


class ClientsDb:
    """Работа с clients.db."""

    def __init__(self, path: str | Path = DB_PATH) -> None:
        self.path = path
        _ensure_dir()
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # --- clients ------------------------------------------------------------

    def upsert_client(
        self, user_id: int, plan: str = "free", status: str = "active"
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO clients (user_id, plan, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   plan=excluded.plan,
                   status=excluded.status,
                   updated_at=excluded.updated_at""",
            (user_id, plan, status, now, now),
        )
        self.conn.commit()

    def get_client(self, user_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM clients WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_clients(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM clients ORDER BY user_id"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, user_id: int, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE clients SET status = ?, updated_at = ? WHERE user_id = ?",
            (status, now, user_id),
        )
        self.conn.commit()

    # --- bindings -----------------------------------------------------------

    def add_binding(
        self, user_id: int, chat_id: int, type_: str, role: str = "member"
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT OR IGNORE INTO bindings
               (user_id, chat_id, type, role, added_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, chat_id, type_, role, now),
        )
        self.conn.commit()

    def get_bindings(self, user_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM bindings WHERE user_id = ? ORDER BY added_at",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.conn.close()
