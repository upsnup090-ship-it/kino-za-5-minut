"""Тесты шифрования токенов."""

from __future__ import annotations

import os

import pytest

from pro_code.crypto import (
    ENV_KEY,
    PREFIX,
    CryptoError,
    TokenCipher,
    generate_key,
    mask,
)

TOKEN = "1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


def test_generate_key_is_fernet():
    key = generate_key()
    assert len(key) == 44
    assert key.endswith("=")


def test_roundtrip():
    c = TokenCipher(generate_key())
    enc = c.encrypt(TOKEN)
    assert enc.startswith(PREFIX)
    assert c.decrypt(enc) == TOKEN


def test_ciphertext_differs_each_time():
    """Fernet использует случайный IV — два шифра одного токена разные."""
    c = TokenCipher(generate_key())
    assert c.encrypt(TOKEN) != c.encrypt(TOKEN)


def test_plaintext_not_in_ciphertext():
    c = TokenCipher(generate_key())
    enc = c.encrypt(TOKEN)
    assert TOKEN not in enc
    assert "AAHdqTcv" not in enc


def test_wrong_key_fails():
    enc = TokenCipher(generate_key()).encrypt(TOKEN)
    with pytest.raises(CryptoError):
        TokenCipher(generate_key()).decrypt(enc)


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)
    with pytest.raises(CryptoError) as exc:
        TokenCipher()
    assert ENV_KEY in str(exc.value)


def test_key_from_env(monkeypatch):
    key = generate_key()
    monkeypatch.setenv(ENV_KEY, key)
    c = TokenCipher()
    assert c.decrypt(c.encrypt(TOKEN)) == TOKEN


def test_bad_key_raises():
    with pytest.raises(CryptoError):
        TokenCipher("не-ключ")


def test_empty_plaintext_rejected():
    c = TokenCipher(generate_key())
    with pytest.raises(CryptoError):
        c.encrypt("")


def test_empty_stored_rejected():
    c = TokenCipher(generate_key())
    with pytest.raises(CryptoError):
        c.decrypt("")


def test_plain_token_passthrough():
    """Запись без префикса (до шифрования) возвращается как есть."""
    c = TokenCipher(generate_key())
    assert c.decrypt(TOKEN) == TOKEN


def test_is_encrypted():
    c = TokenCipher(generate_key())
    assert c.is_encrypted(c.encrypt(TOKEN)) is True
    assert c.is_encrypted(TOKEN) is False
    assert c.is_encrypted("") is False


def test_mask():
    assert mask(TOKEN) == "12345678…"
    assert mask("short") == "…"
    assert mask("") == ""


def test_mask_never_leaks_full_token():
    assert TOKEN not in mask(TOKEN)