"""Тесты активации Auto по ключу: генерация, привязка, ввод в боте клиента."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pro_code.clients_db import ClientsDb
from pro_code.router import _activate_key, build_main_menu, menu_keyboard
from rrA.tokens import generate_key


@pytest.fixture
def db(tmp_path):
    d = ClientsDb(tmp_path / "clients.db")
    yield d
    d.close()


def test_generate_key_format():
    key = generate_key()
    assert key.startswith("KINO-")
    parts = key.split("-")
    assert len(parts) == 4            # KINO + 3 блока
    assert len(parts[1]) == 4
    assert len(parts[2]) == 4
    assert len(parts[3]) == 4


def test_generate_keys_unique():
    keys = {generate_key() for _ in range(50)}
    assert len(keys) == 50


def test_create_and_get_key(db):
    db.upsert_client(1, plan="free")
    key = generate_key()
    db.create_key(1, key)
    assert db.get_key_user(key) == 1
    assert db.get_key_for_user(1) == key


def test_key_multiuse_same_client(db):
    db.upsert_client(1, plan="free")
    key = generate_key()
    db.create_key(1, key)
    # Многоразовый: повторный get того же ключа тот же user.
    assert db.get_key_user(key) == 1
    assert db.get_key_user(key) == 1


def test_regenerate_replaces_key(db):
    db.upsert_client(1, plan="free")
    old = generate_key()
    db.create_key(1, old)
    new = generate_key()
    db.create_key(1, new)
    assert db.get_key_user(old) is None       # старый аннулирован
    assert db.get_key_for_user(1) == new


def test_remove_key(db):
    db.upsert_client(1, plan="free")
    key = generate_key()
    db.create_key(1, key)
    db.remove_key(1)
    assert db.get_key_user(key) is None


def test_activate_correct_key_sets_auto(db):
    """Верный ключ своего клиента → plan=auto."""
    db.upsert_client(1, plan="free")
    key = generate_key()
    db.create_key(1, key)
    sent = []
    with patch("pro_code.router.send_message",
               side_effect=lambda t, c, txt, **k: sent.append(txt) or {"message_id": 1}):
        _activate_key("T", 1, 1, f"/activation {key}", db)
    assert db.get_client(1)["plan"] == "auto"
    assert "Auto активирован" in sent[-1]


def test_activate_wrong_key_rejected(db):
    """Несуществующий ключ → не меняется план."""
    db.upsert_client(1, plan="free")
    sent = []
    with patch("pro_code.router.send_message",
               side_effect=lambda t, c, txt, **k: sent.append(txt) or {"message_id": 1}):
        _activate_key("T", 1, 1, "/activation KINO-AAAA-BBBB-CCCC", db)
    assert db.get_client(1)["plan"] == "free"
    assert "Такого ключа нет" in sent[-1]


def test_activate_foreign_key_rejected(db):
    """Ключ другого клиента → не применяется к этому."""
    db.upsert_client(1, plan="free")
    db.upsert_client(2, plan="free")
    key = generate_key()
    db.create_key(2, key)             # ключ для клиента 2
    sent = []
    with patch("pro_code.router.send_message",
               side_effect=lambda t, c, txt, **k: sent.append(txt) or {"message_id": 1}):
        _activate_key("T", 1, 1, f"/activation {key}", db)  # вводит клиент 1
    assert db.get_client(1)["plan"] == "free"
    assert "другому клиенту" in sent[-1]


def test_activate_bad_format_rejected(db):
    """Ключ не в формате KINO- → инструкция, план не меняется."""
    db.upsert_client(1, plan="free")
    sent = []
    with patch("pro_code.router.send_message",
               side_effect=lambda t, c, txt, **k: sent.append(txt) or {"message_id": 1}):
        _activate_key("T", 1, 1, "/activation abc123", db)
    assert db.get_client(1)["plan"] == "free"
    assert "не похоже на ключ" in sent[-1]


def test_activate_no_arg_shows_help(db):
    """/activation без ключа → подсказка."""
    db.upsert_client(1, plan="free")
    sent = []
    with patch("pro_code.router.send_message",
               side_effect=lambda t, c, txt, **k: sent.append(txt) or {"message_id": 1}):
        _activate_key("T", 1, 1, "/activation", db)
    assert "Введи ключ" in sent[-1]


def test_free_menu_without_key(db):
    """Без ключа меню — free (нет автопостинга), build_main_menu это отражает."""
    kb = menu_keyboard("free")
    labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert "🤖 Автопостинг" not in labels
    assert "✍️ Ручной постинг" in labels


def test_auto_menu_after_key(db):
    """После активации ключом меню — auto (есть кнопка автопостинга/слотов)."""
    kb = menu_keyboard("auto")
    labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert "🤖 Автопостинг / слоты" in labels
