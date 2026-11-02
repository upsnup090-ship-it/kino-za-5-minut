"""Тесты хранилища токенов ботов."""

from __future__ import annotations

import pytest

from pro_code.clients_db import ClientsDb
from pro_code.crypto import CryptoError, TokenCipher, generate_key

TOKEN = "1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


def test_schema_has_bot_tokens(clients_db):
    names = {
        r["name"]
        for r in clients_db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "bot_tokens" in names


def test_set_and_get(clients_db):
    clients_db.upsert_client(1, plan="free")
    clients_db.set_bot_token(1, TOKEN, bot_id=555, bot_username="my_bot")
    assert clients_db.get_bot_token(1) == TOKEN


def test_token_encrypted_in_db(clients_db):
    """Главное: в БД не должно быть открытого токена."""
    clients_db.upsert_client(1, plan="free")
    clients_db.set_bot_token(1, TOKEN)
    raw = clients_db.conn.execute(
        "SELECT token_enc FROM bot_tokens WHERE user_id = 1"
    ).fetchone()["token_enc"]
    assert TOKEN not in raw
    assert "AAHdqTcv" not in raw
    assert raw.startswith("enc:")


def test_get_bot_info_without_token(clients_db):
    clients_db.upsert_client(1, plan="free")
    clients_db.set_bot_token(1, TOKEN, bot_id=555, bot_username="my_bot")
    info = clients_db.get_bot_info(1)
    assert info["bot_username"] == "my_bot"
    assert info["bot_id"] == 555
    assert info["active"] == 1
    assert "token" not in info
    assert "token_enc" not in info


def test_get_missing_token(clients_db):
    assert clients_db.get_bot_token(999) is None
    assert clients_db.get_bot_info(999) is None


def test_update_token(clients_db):
    clients_db.upsert_client(1, plan="free")
    clients_db.set_bot_token(1, TOKEN, bot_username="old")
    clients_db.set_bot_token(1, "999:NEWTOKENvalue_here_1234567890abc", bot_username="new")
    assert clients_db.get_bot_token(1).startswith("999:")
    assert clients_db.get_bot_info(1)["bot_username"] == "new"


def test_list_active_bots(clients_db):
    clients_db.upsert_client(1, plan="free")
    clients_db.upsert_client(2, plan="auto")
    clients_db.set_bot_token(1, TOKEN, bot_username="bot1")
    clients_db.set_bot_token(2, "222:SECONDtoken_here_1234567890abcdef", bot_username="bot2")
    bots = clients_db.list_active_bots()
    assert len(bots) == 2
    assert bots[0]["user_id"] == 1
    assert bots[0]["token"] == TOKEN
    assert bots[1]["bot_username"] == "bot2"


def test_deactivate_bot(clients_db):
    clients_db.upsert_client(1, plan="free")
    clients_db.set_bot_token(1, TOKEN)
    assert clients_db.deactivate_bot(1) is True
    assert clients_db.list_active_bots() == []
    assert clients_db.get_bot_info(1)["active"] == 0


def test_deactivate_missing(clients_db):
    assert clients_db.deactivate_bot(999) is False


def test_delete_bot_token(clients_db):
    clients_db.upsert_client(1, plan="free")
    clients_db.set_bot_token(1, TOKEN)
    assert clients_db.delete_bot_token(1) is True
    assert clients_db.get_bot_token(1) is None


def test_empty_token_rejected(clients_db):
    clients_db.upsert_client(1, plan="free")
    with pytest.raises(ValueError):
        clients_db.set_bot_token(1, "   ")


def test_wrong_key_returns_none(tmp_path):
    """БД, зашифрованная одним ключом, чужим ключом не читается."""
    db1 = ClientsDb(tmp_path / "c.db", cipher=TokenCipher(generate_key()))
    db1.upsert_client(1, plan="free")
    db1.set_bot_token(1, TOKEN)
    db1.close()

    db2 = ClientsDb(tmp_path / "c.db", cipher=TokenCipher(generate_key()))
    try:
        assert db2.get_bot_token(1) is None
        assert db2.list_active_bots() == []
    finally:
        db2.close()


def test_broken_token_skipped_not_fatal(tmp_path):
    """Один битый токен не роняет список остальных."""
    key = generate_key()
    db = ClientsDb(tmp_path / "c.db", cipher=TokenCipher(key))
    db.upsert_client(1, plan="free")
    db.upsert_client(2, plan="free")
    db.set_bot_token(1, TOKEN, bot_username="good")
    db.set_bot_token(2, "222:BADtoken_here_1234567890abcdefgh", bot_username="bad")
    # портим запись второго
    db.conn.execute(
        "UPDATE bot_tokens SET token_enc = 'enc:garbage' WHERE user_id = 2"
    )
    db.conn.commit()
    bots = db.list_active_bots()
    assert len(bots) == 1
    assert bots[0]["user_id"] == 1
    db.close()


def test_lazy_cipher_without_key(tmp_path, monkeypatch):
    """БД открывается без ключа, пока не трогают токены."""
    from pro_code.crypto import ENV_KEY

    monkeypatch.delenv(ENV_KEY, raising=False)
    db = ClientsDb(tmp_path / "c.db")
    try:
        db.upsert_client(1, plan="free")
        assert db.get_client(1)["plan"] == "free"
        with pytest.raises(CryptoError):
            db.set_bot_token(1, TOKEN)
    finally:
        db.close()