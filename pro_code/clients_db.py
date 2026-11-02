"""clients.db — таблица clients (без токенов).

Токены — только в .env, не в БД.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "clients.db"

#: Лимит LLM-расхода на клиента (долларов США)
LLM_SPEND_LIMIT = 1.0


class ClientsDbError(Exception):
    """Ошибка БД клиентов."""


def _ensure_dir() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    user_id         INTEGER PRIMARY KEY,
    plan            TEXT    NOT NULL DEFAULT 'free'
                            CHECK (plan IN ('free', 'auto')),
    status          TEXT    NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'inactive')),
    llm_spent       REAL    NOT NULL DEFAULT 0.0,
    auto_channel_id INTEGER,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
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

CREATE TABLE IF NOT EXISTS client_channels (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES clients(user_id),
    chat_id     INTEGER NOT NULL,
    title       TEXT    NOT NULL DEFAULT '',
    chat_type   TEXT    NOT NULL CHECK (chat_type IN ('channel', 'supergroup', 'chat', 'group')),
    purpose     TEXT    NOT NULL DEFAULT 'manual'
                        CHECK (purpose IN ('auto', 'manual', 'both')),
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

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- clients ------------------------------------------------------------

    def upsert_client(
        self, user_id: int, plan: str = "free", status: str = "active"
    ) -> None:
        now = self._now()
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
        now = self._now()
        self.conn.execute(
            "UPDATE clients SET status = ?, updated_at = ? WHERE user_id = ?",
            (status, now, user_id),
        )
        self.conn.commit()

    # --- LLM spend ----------------------------------------------------------

    def get_llm_spent(self, user_id: int) -> float:
        row = self.conn.execute(
            "SELECT llm_spent FROM clients WHERE user_id = ?", (user_id,)
        ).fetchone()
        return float(row["llm_spent"]) if row else 0.0

    def add_llm_spend(self, user_id: int, amount: float) -> float:
        now = self._now()
        self.conn.execute(
            "UPDATE clients SET llm_spent = llm_spent + ?, updated_at = ? WHERE user_id = ?",
            (amount, now, user_id),
        )
        self.conn.commit()
        return self.get_llm_spent(user_id)

    def reset_llm_spent(self, user_id: int) -> None:
        now = self._now()
        self.conn.execute(
            "UPDATE clients SET llm_spent = 0.0, updated_at = ? WHERE user_id = ?",
            (now, user_id),
        )
        self.conn.commit()

    def can_use_llm(self, user_id: int) -> bool:
        spent = self.get_llm_spent(user_id)
        return spent < LLM_SPEND_LIMIT

    # --- auto_channel_id (канал для автопостинга) ---------------------------

    def set_auto_channel(self, user_id: int, channel_id: int | None) -> None:
        now = self._now()
        self.conn.execute(
            "UPDATE clients SET auto_channel_id = ?, updated_at = ? WHERE user_id = ?",
            (channel_id, now, user_id),
        )
        self.conn.commit()

    def get_auto_channel(self, user_id: int) -> int | None:
        row = self.conn.execute(
            "SELECT auto_channel_id FROM clients WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["auto_channel_id"] if row else None

    # --- client_channels (чаты/каналы клиента для ручного и авто постинга) --

    def add_channel(
        self, user_id: int, chat_id: int, title: str, chat_type: str,
        purpose: str = "manual",
    ) -> None:
        """Добавить канал/чат клиенту. purpose: auto | manual | both."""
        now = self._now()
        self.conn.execute(
            """INSERT OR IGNORE INTO client_channels
               (user_id, chat_id, title, chat_type, purpose, added_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, chat_id, title, chat_type, purpose, now),
        )
        self.conn.commit()

    def set_channel_purpose(
        self, user_id: int, chat_id: int, purpose: str,
    ) -> bool:
        """Изменить назначение канала (auto/manual/both)."""
        now = self._now()
        cur = self.conn.execute(
            "UPDATE client_channels SET purpose = ?, added_at = ? "
            "WHERE user_id = ? AND chat_id = ?",
            (purpose, now, user_id, chat_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def remove_channel(self, user_id: int, chat_id: int) -> bool:
        """Удалить канал/чат клиента."""
        cur = self.conn.execute(
            "DELETE FROM client_channels WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_channels(
        self, user_id: int, purpose: str | None = None,
    ) -> list[dict[str, Any]]:
        """Список каналов клиента. purpose=None — все."""
        if purpose:
            rows = self.conn.execute(
                "SELECT * FROM client_channels WHERE user_id = ? AND "
                "purpose IN (?, 'both') ORDER BY added_at",
                (user_id, purpose),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM client_channels WHERE user_id = ? ORDER BY added_at",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_channel(self, user_id: int, chat_id: int) -> dict[str, Any] | None:
        """Получить один канал."""
        row = self.conn.execute(
            "SELECT * FROM client_channels WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        ).fetchone()
        return dict(row) if row else None

    # --- bindings -----------------------------------------------------------

    def add_binding(
        self, user_id: int, chat_id: int, type_: str, role: str = "member"
    ) -> None:
        now = self._now()
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