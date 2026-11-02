"""БД модуля ручного направления (ruch.db).

Таблицы:
  - repeating_messages — повторяющиеся сообщения (per user)
  - manual_posts — ручные посты (per user)
  - post_stats — статистика постов
  - user_backups — мета-информация о бэкапах пользователя
  - bindings — привязки чатов/каналов/личек (per user)
  - post_drafts — состояние диалога ручного постинга (FSM, per user)
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "ruch.db"


class RuchDbError(Exception):
    """Ошибка БД ручного модуля."""


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS repeating_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    text            TEXT    NOT NULL,
    time            TEXT    NOT NULL,
    interval_hours  INTEGER DEFAULT 24,
    interval_minutes INTEGER DEFAULT 0,
    days_week       TEXT    DEFAULT '0,1,2,3,4,5,6',
    days_month      TEXT    DEFAULT '',
    start_date      TEXT,
    end_date        TEXT,
    pin             INTEGER DEFAULT 0,
    delete_last     INTEGER DEFAULT 0,
    enabled         INTEGER DEFAULT 1,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_repeating_user ON repeating_messages(user_id);

CREATE TABLE IF NOT EXISTS manual_posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    channel_chat_id INTEGER NOT NULL,
    text            TEXT    NOT NULL,
    media_type      TEXT    DEFAULT 'text',
    media_path      TEXT,
    parse_mode      TEXT    DEFAULT 'HTML',
    reply_to_msg_id INTEGER,
    status          TEXT    DEFAULT 'draft',
    created_at      TEXT    NOT NULL,
    sent_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_manual_user ON manual_posts(user_id);
CREATE INDEX IF NOT EXISTS idx_manual_channel ON manual_posts(channel_chat_id);

CREATE TABLE IF NOT EXISTS post_stats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id     INTEGER NOT NULL REFERENCES manual_posts(id),
    user_id     INTEGER NOT NULL,
    message_id  INTEGER,
    views       INTEGER DEFAULT 0,
    forwards    INTEGER DEFAULT 0,
    reactions   TEXT    DEFAULT '[]',
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stats_post ON post_stats(post_id);

CREATE TABLE IF NOT EXISTS user_backups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    filename    TEXT    NOT NULL,
    format      TEXT    NOT NULL CHECK (format IN ('csv', 'txt')),
    size_bytes  INTEGER DEFAULT 0,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backup_user ON user_backups(user_id);

CREATE TABLE IF NOT EXISTS bindings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    chat_id     INTEGER NOT NULL,
    type        TEXT    NOT NULL CHECK (type IN ('chat', 'channel', 'private')),
    role        TEXT    DEFAULT 'member',
    added_at    TEXT    NOT NULL,
    UNIQUE(user_id, chat_id)
);

CREATE INDEX IF NOT EXISTS idx_bindings_user ON bindings(user_id);

CREATE TABLE IF NOT EXISTS post_drafts (
    user_id         INTEGER PRIMARY KEY,
    state           TEXT    NOT NULL DEFAULT 'idle',
    channel_chat_id INTEGER,
    text            TEXT    DEFAULT '',
    media_type      TEXT    DEFAULT 'text',
    media_path      TEXT,
    parse_mode      TEXT    DEFAULT 'HTML',
    reply_to_msg_id INTEGER,
    post_id         INTEGER,
    updated_at      TEXT    NOT NULL
);
"""


class RuchDb:
    """Работа с БД ручного модуля."""

    def __init__(self, path: str | Path = DB_PATH) -> None:
        self.path = Path(path)
        _ensure_dir(self.path)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- Repeating messages -------------------------------------------------

    def add_repeating(
        self,
        user_id: int,
        text: str,
        time: str,
        interval_hours: int = 24,
        interval_minutes: int = 0,
        days_week: str = "0,1,2,3,4,5,6",
        days_month: str = "",
        start_date: str | None = None,
        end_date: str | None = None,
        pin: bool = False,
        delete_last: bool = False,
    ) -> int:
        now = self._now()
        cur = self.conn.execute(
            """INSERT INTO repeating_messages
               (user_id, text, time, interval_hours, interval_minutes,
                days_week, days_month, start_date, end_date,
                pin, delete_last, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (user_id, text, time, interval_hours, interval_minutes,
             days_week, days_month, start_date, end_date,
             int(pin), int(delete_last), now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_repeating(self, msg_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM repeating_messages WHERE id = ?", (msg_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_repeating(self, user_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM repeating_messages WHERE user_id = ? ORDER BY time",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def toggle_repeating(self, msg_id: int) -> bool:
        """Вкл/выкл. Возвращает новое состояние (True = включено)."""
        now = self._now()
        row = self.conn.execute(
            "UPDATE repeating_messages SET enabled = 1 - enabled, updated_at = ? WHERE id = ? RETURNING enabled",
            (now, msg_id),
        ).fetchone()
        if row:
            self.conn.commit()
            return bool(row["enabled"])
        return False

    def delete_repeating(self, msg_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM repeating_messages WHERE id = ?", (msg_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # --- Manual posts -------------------------------------------------------

    def add_manual_post(
        self,
        user_id: int,
        channel_chat_id: int,
        text: str,
        media_type: str = "text",
        media_path: str | None = None,
        parse_mode: str = "HTML",
        reply_to_msg_id: int | None = None,
    ) -> int:
        now = self._now()
        cur = self.conn.execute(
            """INSERT INTO manual_posts
               (user_id, channel_chat_id, text, media_type, media_path,
                parse_mode, reply_to_msg_id, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?)""",
            (user_id, channel_chat_id, text, media_type, media_path,
             parse_mode, reply_to_msg_id, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_manual_posts(
        self, user_id: int, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM manual_posts WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_last_posts(self, user_id: int, n: int = 5) -> list[dict[str, Any]]:
        """Последние N постов пользователя (для бэкапа)."""
        rows = self.conn.execute(
            "SELECT * FROM manual_posts WHERE user_id = ? AND status = 'sent' ORDER BY sent_at DESC LIMIT ?",
            (user_id, n),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_sent(self, post_id: int, message_id: int | None = None) -> None:
        """Отметить пост отправленным. message_id пишется в post_stats.

        Колонки message_id в manual_posts нет (схема зафиксирована baseline),
        поэтому id сообщения живёт в post_stats — там же, где просмотры.
        """
        now = self._now()
        self.conn.execute(
            "UPDATE manual_posts SET status = 'sent', sent_at = ? WHERE id = ?",
            (now, post_id),
        )
        if message_id is not None:
            row = self.conn.execute(
                "SELECT user_id FROM manual_posts WHERE id = ?", (post_id,)
            ).fetchone()
            if row:
                self.conn.execute(
                    "INSERT INTO post_stats (post_id, user_id, message_id, views, "
                    "forwards, reactions, created_at) VALUES (?, ?, ?, 0, 0, '[]', ?)",
                    (post_id, row["user_id"], message_id, now),
                )
        self.conn.commit()

    def mark_failed(self, post_id: int, reason: str = "") -> None:
        """Отметить пост как неотправленный."""
        self.conn.execute(
            "UPDATE manual_posts SET status = 'failed' WHERE id = ?", (post_id,)
        )
        self.conn.commit()

    def get_post(self, post_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM manual_posts WHERE id = ?", (post_id,)
        ).fetchone()
        return dict(row) if row else None

    def delete_post(self, post_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM manual_posts WHERE id = ?", (post_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def get_message_id(self, post_id: int) -> int | None:
        """id сообщения в Telegram для поста (из post_stats)."""
        row = self.conn.execute(
            "SELECT message_id FROM post_stats WHERE post_id = ? AND message_id IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (post_id,),
        ).fetchone()
        return row["message_id"] if row else None

    # --- Post stats ---------------------------------------------------------

    def add_stat(
        self,
        post_id: int,
        user_id: int,
        message_id: int | None = None,
        views: int = 0,
        forwards: int = 0,
        reactions: list | None = None,
    ) -> int:
        now = self._now()
        cur = self.conn.execute(
            "INSERT INTO post_stats (post_id, user_id, message_id, views, forwards, reactions, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (post_id, user_id, message_id, views, forwards,
             json.dumps(reactions or []), now),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_stats(self, user_id: int) -> dict[str, Any]:
        """Агрегированная статистика пользователя."""
        total_posts = self.conn.execute(
            "SELECT COUNT(*) AS n FROM manual_posts WHERE user_id = ? AND status = 'sent'",
            (user_id,),
        ).fetchone()["n"]
        total_views = self.conn.execute(
            "SELECT COALESCE(SUM(views), 0) AS s FROM post_stats WHERE user_id = ?",
            (user_id,),
        ).fetchone()["s"]
        total_forwards = self.conn.execute(
            "SELECT COALESCE(SUM(forwards), 0) AS s FROM post_stats WHERE user_id = ?",
            (user_id,),
        ).fetchone()["s"]
        return {
            "total_posts": total_posts,
            "total_views": total_views,
            "total_forwards": total_forwards,
        }

    # --- User backups -------------------------------------------------------

    def save_backup_meta(
        self, user_id: int, filename: str, fmt: str, size_bytes: int
    ) -> int:
        now = self._now()
        cur = self.conn.execute(
            "INSERT INTO user_backups (user_id, filename, format, size_bytes, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, filename, fmt, size_bytes, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_backups(self, user_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM user_backups WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def clean_old_backups(
        self, user_id: int, keep: int = 2, backup_dir: Path | None = None
    ) -> None:
        """Удаляет старые бэкапы пользователя, оставляя keep последних.

        ``backup_dir`` — каталог, где лежат файлы (боевой путь или явный).
        Без него — ``user_backups`` рядом с БД.
        """
        rows = self.conn.execute(
            "SELECT id, filename FROM user_backups WHERE user_id = ? ORDER BY created_at DESC, id DESC",
            (user_id,),
        ).fetchall()
        if len(rows) <= keep:
            return
        candidates = [
            backup_dir,
            Path(self.path).parent / "user_backups",
            Path(__file__).resolve().parents[1] / "data" / "user_backups",
        ]
        for row in rows[keep:]:
            self.conn.execute("DELETE FROM user_backups WHERE id = ?", (row["id"],))
            for base in candidates:
                if base is None:
                    continue
                fpath = base / row["filename"]
                if fpath.exists():
                    fpath.unlink()
                    break
        self.conn.commit()

    # --- Bindings (дублируются из clients.db для быстрого доступа) ----------

    def save_binding(
        self, user_id: int, chat_id: int, type_: str, role: str = "member"
    ) -> None:
        """Сохранить привязку. Повтор по (user_id, chat_id) — no-op.

        Не ``INSERT OR IGNORE``: тот глотает и нарушение CHECK по type,
        и дубль. Здесь дубль гасится ON CONFLICT, а неверный тип падает.
        """
        now = self._now()
        self.conn.execute(
            """INSERT INTO bindings
               (user_id, chat_id, type, role, added_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, chat_id) DO NOTHING""",
            (user_id, chat_id, type_, role, now),
        )
        self.conn.commit()

    def list_bindings(self, user_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM bindings WHERE user_id = ? ORDER BY added_at",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Post drafts (FSM ручного постинга) ---------------------------------

    def get_draft(self, user_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM post_drafts WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def save_draft(self, user_id: int, **fields: Any) -> None:
        """Сохранить состояние диалога. Поля: state, channel_chat_id, text,
        media_type, media_path, parse_mode, reply_to_msg_id, post_id."""
        now = self._now()
        allowed = (
            "state", "channel_chat_id", "text", "media_type", "media_path",
            "parse_mode", "reply_to_msg_id", "post_id",
        )
        current = self.get_draft(user_id) or {}
        merged = {k: fields.get(k, current.get(k)) for k in allowed}
        self.conn.execute(
            """INSERT INTO post_drafts
               (user_id, state, channel_chat_id, text, media_type, media_path,
                parse_mode, reply_to_msg_id, post_id, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   state=excluded.state,
                   channel_chat_id=excluded.channel_chat_id,
                   text=excluded.text,
                   media_type=excluded.media_type,
                   media_path=excluded.media_path,
                   parse_mode=excluded.parse_mode,
                   reply_to_msg_id=excluded.reply_to_msg_id,
                   post_id=excluded.post_id,
                   updated_at=excluded.updated_at""",
            (
                user_id,
                merged.get("state") or "idle",
                merged.get("channel_chat_id"),
                merged.get("text") or "",
                merged.get("media_type") or "text",
                merged.get("media_path"),
                merged.get("parse_mode") or "HTML",
                merged.get("reply_to_msg_id"),
                merged.get("post_id"),
                now,
            ),
        )
        self.conn.commit()

    def clear_draft(self, user_id: int) -> None:
        self.conn.execute("DELETE FROM post_drafts WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
