"""Фикстуры тестов ручного модуля.

Сеть не используется: все вызовы Bot API замоканы. БД — временные файлы,
удаляются после теста.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Корень репозитория в sys.path — тесты запускаются из /workspace.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pro_code.clients_db import ClientsDb  # noqa: E402
from project_ruch.ruch_db import RuchDb  # noqa: E402


@pytest.fixture
def ruch_db(tmp_path):
    """Временная ruch.db."""
    db = RuchDb(tmp_path / "ruch.db")
    yield db
    db.close()


@pytest.fixture
def clients_db(tmp_path):
    """Временная clients.db."""
    db = ClientsDb(tmp_path / "clients.db")
    yield db
    db.close()


@pytest.fixture
def client_with_channel(clients_db):
    """Клиент free с одним каналом для ручного постинга."""
    clients_db.upsert_client(1, plan="free")
    clients_db.add_channel(1, -100123, "Test Channel", "channel", purpose="manual")
    return clients_db


@pytest.fixture
def sent_messages():
    """Список отправленных сообщений (мок send_message)."""
    return []


@pytest.fixture
def mock_send(sent_messages):
    """Мок send_message: запоминает текст, возвращает message_id."""
    from unittest.mock import patch

    def _fake(token, chat_id, text, **kwargs):
        sent_messages.append({"chat_id": chat_id, "text": text, **kwargs})
        return {"message_id": len(sent_messages)}

    with patch("project_ruch.dialog.send_message", side_effect=_fake), \
         patch("pro_code.router.send_message", side_effect=_fake):
        yield sent_messages
