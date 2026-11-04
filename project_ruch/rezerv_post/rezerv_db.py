"""БД отложенных постов (rezerv.db).

Таблица: reserve — per-client, для ручного и авто.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pro_code.db import ThreadedConn, ensure_dir

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "rezerv.db"


class ReserveDbError(Exception):
    """Ошибка БД резерва."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS reserve (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    source      TEXT    NOT NULL DEFAULT 'manual',
    text        TEXT    NOT NULL,
    media_type  TEXT    DEFAULT 'text',
    media_path  TEXT,
    parse_mode  TEXT    DEFAULT 'HTML',
    scheduled_at TEXT,
    status      TEXT    DEFAULT 'pending',
    created_at  TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL,
    used_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_reserve_user ON reserve(user_id);
CREATE INDEX IF NOT EXISTS idx_reserve_status ON reserve(status);
CREATE INDEX IF NOT EXISTS idx_reserve_expires ON reserve(expires_at);
"""


class ReserveDb:
    """Работа с БД отложенных постов."""

    def __init__(self, path: str | Path | None = None) -> None:
        # Дефолт вычисляется в момент вызова: подмена DB_PATH (тесты) должна
        # действовать, а строковый путь не должен падать на .parent.
        self.path = Path(path) if path is not None else DB_PATH
        ensure_dir(self.path)
        # Соединение — на поток (pro_code/db.py): резерв открывается и в
        # диалоге, и в планировщике, то есть из разных потоков.
        self._conn = ThreadedConn(self.path, on_open=self._init_schema)

    @property
    def conn(self) -> sqlite3.Connection:
        """Соединение текущего потока (открывается при первом обращении)."""
        return self._conn.conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        """Создать схему при открытии соединения (идемпотентно)."""
        conn.executescript(_SCHEMA)
        conn.commit()
        self.conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def add(
        self,
        user_id: int,
        text: str,
        source: str = "manual",
        media_type: str = "text",
        media_path: str | None = None,
        parse_mode: str = "HTML",
        scheduled_at: str | None = None,
    ) -> int:
        now = self._now()
        expires = (
            datetime.now(timezone.utc) + timedelta(hours=48)
        ).isoformat()
        cur = self.conn.execute(
            """INSERT INTO reserve
               (user_id, source, text, media_type, media_path, parse_mode,
                scheduled_at, status, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (user_id, source, text, media_type, media_path, parse_mode,
             scheduled_at, now, expires),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_pending(self, user_id: int) -> list[dict[str, Any]]:
        now = self._now()
        rows = self.conn.execute(
            "SELECT * FROM reserve WHERE user_id = ? AND status = 'pending' AND expires_at > ? ORDER BY created_at",
            (user_id, now),
        ).fetchall()
        return [dict(r) for r in rows]

    def get(self, reserve_id: int) -> dict[str, Any] | None:
        """Один отложенный пост по id (любой статус)."""
        row = self.conn.execute(
            "SELECT * FROM reserve WHERE id = ?", (reserve_id,)
        ).fetchone()
        return dict(row) if row else None

    def take_next(self, user_id: int) -> dict[str, Any] | None:
        now = self._now()
        row = self.conn.execute(
            "SELECT * FROM reserve WHERE user_id = ? AND status = 'pending' AND expires_at > ? ORDER BY created_at LIMIT 1",
            (user_id, now),
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE reserve SET status = 'used', used_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            self.conn.commit()
            return dict(row)
        return None

    def count_pending(self, user_id: int) -> int:
        now = self._now()
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM reserve WHERE user_id = ? AND status = 'pending' AND expires_at > ?",
            (user_id, now),
        ).fetchone()
        return row["n"] if row else 0

    def delete(self, reserve_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM reserve WHERE id = ?", (reserve_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def purge_expired(self) -> int:
        now = self._now()
        cur = self.conn.execute(
            "DELETE FROM reserve WHERE expires_at <= ? OR status = 'used'",
            (now,),
        )
        self.conn.commit()
        return cur.rowcount

    def close(self) -> None:
        """Закрыть соединение текущего потока."""
        self._conn.close()

    def close_all(self) -> None:
        """Закрыть все соединения (остановка процесса)."""
        self._conn.close_all()
