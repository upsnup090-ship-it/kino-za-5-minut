"""clients.db — клиенты, привязки, каналы, токены ботов.

Токены ботов лежат в ``bot_tokens.token_enc`` зашифрованными
(``pro_code/crypto.py``, Fernet). Ключ — в ``.env``, отдельно от БД.
Открытым текстом токен не пишется никуда.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pro_code.crypto import CryptoError, TokenCipher
from pro_code.db import ThreadedConn, ensure_dir

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "clients.db"

#: Лимит LLM-расхода на клиента (долларов США)
LLM_SPEND_LIMIT = 1.0


class ClientsDbError(Exception):
    """Ошибка БД клиентов."""


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

-- Токены ботов клиентов. token_enc — шифротекст (pro_code/crypto.py),
-- открытым текстом токен в БД не лежит никогда.
CREATE TABLE IF NOT EXISTS bot_tokens (
    user_id         INTEGER PRIMARY KEY REFERENCES clients(user_id),
    token_enc       TEXT    NOT NULL,
    bot_id          INTEGER,
    bot_username    TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    added_at        TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);
"""


class ClientsDb:
    """Работа с clients.db."""

    def __init__(
        self, path: str | Path | None = None, cipher: TokenCipher | None = None
    ) -> None:
        # Дефолт берётся в момент вызова, а не при определении функции:
        # иначе подмена DB_PATH (тесты, миграции) не действует.
        self.path = Path(path) if path is not None else DB_PATH
        ensure_dir(self.path)
        # Соединение — на поток, открывается лениво при первом обращении
        # (см. pro_code/db.py). Раньше здесь открывался self.conn в главном
        # потоке, и любой бот в своём потоке получал ProgrammingError.
        self._conn = ThreadedConn(self.path, on_open=self._init_schema)
        # Шифр создаётся лениво: БД нужна и там, где токены не читаются
        # (тесты, миграции). Без ключа падает только работа с токенами.
        self._cipher = cipher

    @property
    def conn(self) -> sqlite3.Connection:
        """Соединение текущего потока (открывается при первом обращении)."""
        return self._conn.conn

    @property
    def cipher(self) -> TokenCipher:
        """Шифр токенов. Создаётся при первом обращении."""
        if self._cipher is None:
            self._cipher = TokenCipher()
        return self._cipher

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        """Создать схему при открытии соединения (идемпотентно).

        Выполняется один раз на каждое новое соединение потока:
        ``CREATE TABLE IF NOT EXISTS`` безопасен при повторе.
        """
        conn.executescript(_SCHEMA)
        conn.commit()

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

    # --- bot_tokens (токены клиентских ботов, зашифрованы) ------------------

    def set_bot_token(
        self,
        user_id: int,
        token: str,
        bot_id: int | None = None,
        bot_username: str | None = None,
    ) -> None:
        """Сохранить токен бота клиента. Токен шифруется перед записью."""
        if not token or not token.strip():
            raise ValueError("пустой токен не сохраняется")
        enc = self.cipher.encrypt(token.strip())
        now = self._now()
        self.conn.execute(
            """INSERT INTO bot_tokens
               (user_id, token_enc, bot_id, bot_username, active, added_at, updated_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   token_enc=excluded.token_enc,
                   bot_id=excluded.bot_id,
                   bot_username=excluded.bot_username,
                   active=1,
                   updated_at=excluded.updated_at""",
            (user_id, enc, bot_id, bot_username, now, now),
        )
        self.conn.commit()

    def get_bot_token(self, user_id: int) -> str | None:
        """Расшифрованный токен бота клиента. None — токена нет."""
        row = self.conn.execute(
            "SELECT token_enc FROM bot_tokens WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return None
        try:
            return self.cipher.decrypt(row["token_enc"])
        except CryptoError as exc:
            logger.error("токен user_id=%d не расшифрован: %s", user_id, exc)
            return None

    def get_bot_info(self, user_id: int) -> dict[str, Any] | None:
        """Метаданные бота без расшифровки токена."""
        row = self.conn.execute(
            "SELECT user_id, bot_id, bot_username, active, added_at, updated_at "
            "FROM bot_tokens WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_active_bots(self) -> list[dict[str, Any]]:
        """Все активные боты с расшифрованными токенами.

        Запись, которую не удалось расшифровать, пропускается с ошибкой в
        лог: один битый токен не должен ронять весь диспетчер.
        """
        rows = self.conn.execute(
            "SELECT user_id, token_enc, bot_id, bot_username FROM bot_tokens "
            "WHERE active = 1 ORDER BY user_id"
        ).fetchall()
        bots: list[dict[str, Any]] = []
        for row in rows:
            try:
                token = self.cipher.decrypt(row["token_enc"])
            except CryptoError as exc:
                logger.error("бот user_id=%d пропущен: %s", row["user_id"], exc)
                continue
            bots.append({
                "user_id": row["user_id"],
                "token": token,
                "bot_id": row["bot_id"],
                "bot_username": row["bot_username"],
            })
        return bots

    def deactivate_bot(self, user_id: int) -> bool:
        """Отключить бота клиента (polling не поднимется)."""
        now = self._now()
        cur = self.conn.execute(
            "UPDATE bot_tokens SET active = 0, updated_at = ? WHERE user_id = ?",
            (now, user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def delete_bot_token(self, user_id: int) -> bool:
        """Удалить токен бота клиента."""
        cur = self.conn.execute(
            "DELETE FROM bot_tokens WHERE user_id = ?", (user_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        """Закрыть соединение текущего потока."""
        self._conn.close()

    def close_all(self) -> None:
        """Закрыть все соединения (остановка процесса)."""
        self._conn.close_all()