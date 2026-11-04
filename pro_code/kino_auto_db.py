"""БД автопостинга (kino_auto.db) — per-client расписание, резерв, прогоны.

Таблицы (все с user_id):
  - schedule_slots  — расписание слотов для клиента
  - reserve         — отложенные посты клиента
  - runs            — журнал прогонов клиента
  - meta            — служебные ключи (пауза, состояние)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pro_code.db import ThreadedConn, ensure_dir

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "kino_auto.db"


class KinoAutoDbError(Exception):
    """Ошибка БД автопостинга."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule_slots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    weekday     INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    time        TEXT    NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE(user_id, weekday, time)
);

CREATE INDEX IF NOT EXISTS idx_slots_user ON schedule_slots(user_id);

CREATE TABLE IF NOT EXISTS reserve (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    link        TEXT    NOT NULL DEFAULT '',
    title       TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT '',
    format      TEXT    NOT NULL DEFAULT '',
    draft_json  TEXT    NOT NULL DEFAULT '{}',
    caption     TEXT    NOT NULL DEFAULT '',
    image_url   TEXT    NOT NULL DEFAULT '',
    kind        TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL,
    used_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_reserve_user ON reserve(user_id);
CREATE INDEX IF NOT EXISTS idx_reserve_expires ON reserve(expires_at);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    slot_id       INTEGER,
    slot_at       TEXT    NOT NULL,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    outcome       TEXT    NOT NULL,
    reason        TEXT    NOT NULL DEFAULT '',
    link          TEXT,
    format        TEXT,
    kind          TEXT,
    is_manual     INTEGER NOT NULL DEFAULT 0,
    caption_len   INTEGER,
    quality_score REAL,
    llm_cost      REAL    DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);

-- Дедуп ссылок per-client: у каждого клиента свой список уже вышедших
-- материалов (решение оператора: контент «свой на клиента»).
CREATE TABLE IF NOT EXISTS used_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    link        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    UNIQUE(user_id, link)
);

CREATE INDEX IF NOT EXISTS idx_used_links_user ON used_links(user_id);

CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


class KinoAutoDb:
    """Работа с БД автопостинга."""

    def __init__(self, path: str | Path | None = None) -> None:
        # Дефолт вычисляется в момент вызова: раньше он подставлялся при
        # определении функции, и подмена DB_PATH (тесты) не действовала —
        # а со строковым путём падало AttributeError 'str' has no 'parent'.
        self.path = Path(path) if path is not None else DB_PATH
        ensure_dir(self.path)
        # Соединение — на поток (pro_code/db.py): serve-цикл и диалоги
        # работают с БД из разных потоков.
        self._conn = ThreadedConn(self.path, on_open=self._init_schema)

    @property
    def conn(self) -> sqlite3.Connection:
        """Соединение текущего потока (открывается при первом обращении)."""
        return self._conn.conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        """Создать схему при открытии соединения (идемпотентно)."""
        conn.executescript(_SCHEMA)
        conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- Slots --------------------------------------------------------------

    def list_slots(self, user_id: int, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM schedule_slots WHERE user_id = ?"
        if enabled_only:
            sql += " AND enabled = 1"
        sql += " ORDER BY weekday, time"
        rows = self.conn.execute(sql, (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def add_slot(self, user_id: int, weekday: int, time: str) -> dict[str, Any]:
        now = self._now()
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO schedule_slots (user_id, weekday, time, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (user_id, weekday, time, now, now),
        )
        self.conn.commit()
        return {"id": cur.lastrowid, "user_id": user_id, "weekday": weekday, "time": time}

    def delete_slot(self, slot_id: int, *, user_id: int | None = None) -> bool:
        """Удалить слот. С user_id — только слот этого клиента (клиентский UI)."""
        sql = "DELETE FROM schedule_slots WHERE id = ?"
        args: list[Any] = [slot_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            args.append(user_id)
        cur = self.conn.execute(sql, tuple(args))
        self.conn.commit()
        return cur.rowcount > 0

    def set_slot_enabled(self, slot_id: int, enabled: bool, *, user_id: int | None = None) -> bool:
        """Вкл/выкл слота. С user_id — только слот этого клиента."""
        now = self._now()
        sql = "UPDATE schedule_slots SET enabled = ?, updated_at = ? WHERE id = ?"
        args: list[Any] = [int(enabled), now, slot_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            args.append(user_id)
        cur = self.conn.execute(sql, tuple(args))
        self.conn.commit()
        return cur.rowcount > 0

    # --- Reserve ------------------------------------------------------------

    def add_reserve(
        self, user_id: int, *,
        link: str, title: str, source: str, format: str,
        draft_json: str, caption: str, image_url: str, kind: str,
        ttl_hours: int = 48,
    ) -> int:
        now = self._now()
        expires = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
        cur = self.conn.execute(
            "INSERT INTO reserve (user_id, link, title, source, format, draft_json, "
            "caption, image_url, kind, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, link, title, source, format, draft_json,
             caption, image_url, kind, now, expires),
        )
        self.conn.commit()
        return cur.lastrowid

    def take_reserve(self, user_id: int) -> dict[str, Any] | None:
        now = self._now()
        row = self.conn.execute(
            "SELECT * FROM reserve WHERE user_id = ? AND used_at IS NULL AND expires_at > ? "
            "ORDER BY created_at LIMIT 1",
            (user_id, now),
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE reserve SET used_at = ? WHERE id = ? AND used_at IS NULL",
                (now, row["id"]),
            )
            self.conn.commit()
            return dict(row)
        return None

    def count_reserve(self, user_id: int, *, only_available: bool = False) -> int:
        now = self._now()
        if only_available:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM reserve WHERE user_id = ? AND used_at IS NULL AND expires_at > ?",
                (user_id, now),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM reserve WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row["n"] if row else 0

    # --- Runs ---------------------------------------------------------------

    def start_run(self, user_id: int, slot_id: int | None, slot_at: str) -> int:
        now = self._now()
        cur = self.conn.execute(
            "INSERT INTO runs (user_id, slot_id, slot_at, started_at, outcome) "
            "VALUES (?, ?, ?, ?, 'running')",
            (user_id, slot_id, slot_at, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(
        self, run_id: int, *,
        outcome: str, reason: str = "", link: str | None = None,
        format: str | None = None, kind: str | None = None,
        caption_len: int | None = None, quality_score: float | None = None,
        llm_cost: float = 0.0,
    ) -> None:
        now = self._now()
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, outcome = ?, reason = ?, "
            "link = ?, format = ?, kind = ?, caption_len = ?, "
            "quality_score = ?, llm_cost = ? WHERE id = ?",
            (now, outcome, reason, link, format, kind,
             caption_len, quality_score, llm_cost, run_id),
        )
        self.conn.commit()

    def recent_runs(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM runs WHERE user_id = ? ORDER BY started_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def slot_done(self, user_id: int, slot_id: int, slot_at: str) -> bool:
        row = self.conn.execute(
            "SELECT id FROM runs WHERE user_id = ? AND slot_id = ? AND slot_at = ?",
            (user_id, slot_id, slot_at),
        ).fetchone()
        return row is not None

    # --- Used links (дедуп ссылок per-client) -------------------------------

    def has_link(self, user_id: int, link: str) -> bool:
        """Публиковалась ли этому клиенту такая ссылка."""
        if not link:
            return False
        row = self.conn.execute(
            "SELECT 1 FROM used_links WHERE user_id = ? AND link = ?",
            (user_id, link),
        ).fetchone()
        return row is not None

    def mark_link(self, user_id: int, link: str) -> None:
        """Отметить ссылку как опубликованную клиенту."""
        if not link:
            return
        now = self._now()
        self.conn.execute(
            "INSERT OR IGNORE INTO used_links (user_id, link, created_at) "
            "VALUES (?, ?, ?)",
            (user_id, link, now),
        )
        self.conn.commit()

    # --- Meta ---------------------------------------------------------------

    def set_meta(self, key: str, value: str) -> None:
        now = self._now()
        self.conn.execute(
            "INSERT INTO meta (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, now),
        )
        self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,),
        ).fetchone()
        return row["value"] if row else None

    def is_paused(self, user_id: int) -> bool:
        val = self.get_meta(f"paused_{user_id}")
        return val == "1"

    def set_paused(self, user_id: int, paused: bool) -> None:
        self.set_meta(f"paused_{user_id}", "1" if paused else "0")

    def close(self) -> None:
        """Закрыть соединение текущего потока."""
        self._conn.close()

    def close_all(self) -> None:
        """Закрыть все соединения (остановка процесса)."""
        self._conn.close_all()
