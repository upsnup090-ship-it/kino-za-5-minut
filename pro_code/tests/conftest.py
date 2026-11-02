"""Фикстуры тестов диспетчера.

Сеть не используется: Bot API замокан. БД — временные файлы.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pro_code.clients_db import ClientsDb  # noqa: E402
from pro_code.crypto import TokenCipher, generate_key  # noqa: E402


@pytest.fixture
def cipher():
    """Шифр с одноразовым ключом."""
    return TokenCipher(generate_key())


@pytest.fixture
def clients_db(tmp_path, cipher):
    """Временная clients.db с шифром."""
    db = ClientsDb(tmp_path / "clients.db", cipher=cipher)
    yield db
    db.close()


@pytest.fixture
def sent():
    """Список отправленных сообщений."""
    return []


@pytest.fixture
def mock_send(sent):
    """Мок send_message: запоминает текст и reply_markup."""
    from unittest.mock import patch

    def _fake(token, chat_id, text, **kwargs):
        sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return {"message_id": len(sent)}

    with patch("pro_code.activation.send_message", side_effect=_fake), \
         patch("pro_code.router.send_message", side_effect=_fake):
        yield sent


@pytest.fixture
def spawner():
    """Подменный запуск бота: запоминает (user_id, token)."""
    from pro_code import activation

    calls: list[tuple[int, str]] = []
    activation.set_spawner(lambda uid, tok: calls.append((uid, tok)))
    yield calls
    activation.set_spawner(None)


@pytest.fixture(autouse=True)
def clean_pending():
    """Сбросить состояние активации между тестами."""
    from pro_code import activation

    yield
    activation._pending.clear()