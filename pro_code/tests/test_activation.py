"""Тесты активации бота клиента через главного бота."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pro_code import activation
from pro_code.tg_api import TgError

TOKEN = "1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
BOT_INFO = {"id": 555, "username": "client_bot"}


def _start(clients_db, mock_send, user_id=1):
    activation.handle("M", user_id, user_id, "/start", clients_db)


def test_start_asks_for_token(clients_db, mock_send):
    _start(clients_db, mock_send)
    assert "BotFather" in mock_send[-1]["text"]
    assert activation.is_pending(1) is True


def test_start_has_cancel_button(clients_db, mock_send):
    _start(clients_db, mock_send)
    markup = mock_send[-1]["reply_markup"]
    assert markup["inline_keyboard"][0][0]["callback_data"] == "cb:/cancel"


def test_valid_token_saved_and_spawned(clients_db, mock_send, spawner):
    _start(clients_db, mock_send)
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    assert clients_db.get_bot_token(1) == TOKEN
    assert clients_db.get_bot_info(1)["bot_username"] == "client_bot"
    assert spawner == [(1, TOKEN)]
    assert activation.is_pending(1) is False


def test_success_message_mentions_bot(clients_db, mock_send, spawner):
    _start(clients_db, mock_send)
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    assert "@client_bot" in mock_send[-1]["text"]


def test_token_not_in_logs_or_messages(clients_db, mock_send, spawner):
    """Токен целиком не должен попасть в ответ клиенту."""
    _start(clients_db, mock_send)
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    assert TOKEN not in mock_send[-1]["text"]


def test_bad_format_rejected(clients_db, mock_send):
    _start(clients_db, mock_send)
    activation.handle("M", 1, 1, "не токен", clients_db)
    assert "не похоже на токен" in mock_send[-1]["text"]
    assert activation.is_pending(1) is True
    assert clients_db.get_bot_token(1) is None


def test_short_token_rejected(clients_db, mock_send):
    _start(clients_db, mock_send)
    activation.handle("M", 1, 1, "123:short", clients_db)
    assert "не похоже" in mock_send[-1]["text"]


def test_token_without_colon_rejected(clients_db, mock_send):
    _start(clients_db, mock_send)
    activation.handle("M", 1, 1, "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw", clients_db)
    assert "не похоже" in mock_send[-1]["text"]


def test_telegram_rejects_token(clients_db, mock_send, spawner):
    _start(clients_db, mock_send)
    with patch("pro_code.activation.get_me", side_effect=TgError("401 Unauthorized")):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    assert "не принял" in mock_send[-1]["text"]
    assert clients_db.get_bot_token(1) is None
    assert spawner == []
    assert activation.is_pending(1) is True


def test_cancel_resets(clients_db, mock_send):
    _start(clients_db, mock_send)
    activation.handle("M", 1, 1, "/cancel", clients_db)
    assert activation.is_pending(1) is False
    assert "отменена" in mock_send[-1]["text"]


def test_text_after_cancel_not_absorbed(clients_db, mock_send):
    _start(clients_db, mock_send)
    activation.handle("M", 1, 1, "/cancel", clients_db)
    assert activation.handle("M", 1, 1, "привет", clients_db) is False


def test_already_activated_shows_status(clients_db, mock_send, spawner):
    _start(clients_db, mock_send)
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    _start(clients_db, mock_send)
    assert "уже активирован" in mock_send[-1]["text"]
    assert "@client_bot" in mock_send[-1]["text"]


def test_replace_token(clients_db, mock_send, spawner):
    _start(clients_db, mock_send)
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    activation.handle("M", 1, 1, "/replace_token", clients_db)
    assert activation.is_pending(1) is True
    assert "новый токен" in mock_send[-1]["text"]


def test_my_bots_empty(clients_db, mock_send):
    activation.handle("M", 1, 1, "/my_bots", clients_db)
    assert "нет активированных" in mock_send[-1]["text"]


def test_my_bots_shows_bot(clients_db, mock_send, spawner):
    _start(clients_db, mock_send)
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    activation.handle("M", 1, 1, "/my_bots", clients_db)
    assert "@client_bot" in mock_send[-1]["text"]
    assert "free" in mock_send[-1]["text"]


def test_spawn_failure_still_saves(clients_db, mock_send):
    """Если поток не поднялся, токен всё равно сохранён."""
    from pro_code import activation as act

    def boom(uid, tok):
        raise RuntimeError("поток не поднялся")

    act.set_spawner(boom)
    try:
        _start(clients_db, mock_send)
        with patch("pro_code.activation.get_me", return_value=BOT_INFO):
            activation.handle("M", 1, 1, TOKEN, clients_db)
        assert clients_db.get_bot_token(1) == TOKEN
        assert "активирован" in mock_send[-1]["text"]
    finally:
        act.set_spawner(None)


def test_crypto_error_reported(clients_db, mock_send, spawner, monkeypatch):
    """Без ключа шифрования токен не сохраняется, клиент получает понятный ответ."""
    from pro_code.crypto import ENV_KEY

    monkeypatch.delenv(ENV_KEY, raising=False)
    clients_db._cipher = None  # заставит создать шифр заново
    _start(clients_db, mock_send)
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    assert "ключ" in mock_send[-1]["text"].lower() or "мастер" in mock_send[-1]["text"].lower()
    assert spawner == []


def test_isolated_per_user(clients_db, mock_send, spawner):
    activation.handle("M", 1, 1, "/start", clients_db)
    activation.handle("M", 2, 2, "/start", clients_db)
    assert activation.is_pending(1) is True
    assert activation.is_pending(2) is True
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    assert activation.is_pending(1) is False
    assert activation.is_pending(2) is True