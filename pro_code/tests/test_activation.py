"""Тесты активации бота клиента через главного бота.

Флоу (2026-11-04, решение оператора):
  /start → приветствие + кнопка «Подключить моего бота» (pending НЕ ставится)
  cb:/begin_token → инструкция BotFather + ожидание токена
  токен вслепую (без кнопки) → подсказка «нажми кнопку»
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pro_code import activation
from pro_code.tg_api import TgError

TOKEN = "1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
BOT_INFO = {"id": 555, "username": "client_bot"}


def _start(clients_db, mock_send, user_id=1):
    """Начальный /start: приветствие с кнопкой, без pending."""
    activation.handle("M", user_id, user_id, "/start", clients_db)


def _begin(clients_db, mock_send, user_id=1):
    """Нажатие кнопки «Подключить моего бота»."""
    activation.handle("M", user_id, user_id, "/begin_token", clients_db)


# --- /start -------------------------------------------------------------

def test_start_shows_welcome_with_button(clients_db, mock_send):
    _start(clients_db, mock_send)
    assert "Киномодули" in mock_send[-1]["text"]
    assert activation.is_pending(1) is False


def test_start_has_connect_button(clients_db, mock_send):
    _start(clients_db, mock_send)
    markup = mock_send[-1]["reply_markup"]
    datas = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert "cb:/begin_token" in datas


def test_begin_token_asks_and_pends(clients_db, mock_send):
    _begin(clients_db, mock_send)
    assert "BotFather" in mock_send[-1]["text"]
    assert activation.is_pending(1) is True


def test_begin_has_cancel_button(clients_db, mock_send):
    _begin(clients_db, mock_send)
    markup = mock_send[-1]["reply_markup"]
    assert markup["inline_keyboard"][0][0]["callback_data"] == "cb:/cancel"


def test_token_without_button_hint(clients_db, mock_send):
    """Токен без нажатия кнопки не принимается — подсказка."""
    activation.handle("M", 1, 1, TOKEN, clients_db)
    assert "Подключить" in mock_send[-1]["text"]
    assert clients_db.get_bot_token(1) is None


# --- ввод токена после кнопки -------------------------------------------

def test_valid_token_saved_and_spawned(clients_db, mock_send, spawner):
    _begin(clients_db, mock_send)
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    assert clients_db.get_bot_token(1) == TOKEN
    assert clients_db.get_bot_info(1)["bot_username"] == "client_bot"
    assert spawner == [(1, TOKEN)]
    assert activation.is_pending(1) is False


def test_success_message_mentions_bot(clients_db, mock_send, spawner):
    _begin(clients_db, mock_send)
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    assert "@client\\_bot" in mock_send[-1]["text"]


def test_token_not_in_logs_or_messages(clients_db, mock_send, spawner):
    """Токен целиком не должен попасть в ответ клиенту."""
    _begin(clients_db, mock_send)
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    assert TOKEN not in mock_send[-1]["text"]


def test_bad_format_rejected(clients_db, mock_send):
    _begin(clients_db, mock_send)
    activation.handle("M", 1, 1, "не токен", clients_db)
    assert "не похоже на токен" in mock_send[-1]["text"]
    assert activation.is_pending(1) is True
    assert clients_db.get_bot_token(1) is None


def test_short_token_rejected(clients_db, mock_send):
    _begin(clients_db, mock_send)
    activation.handle("M", 1, 1, "123:short", clients_db)
    assert "не похоже" in mock_send[-1]["text"]


def test_token_without_colon_rejected(clients_db, mock_send):
    _begin(clients_db, mock_send)
    activation.handle("M", 1, 1, "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw", clients_db)
    assert "не похоже" in mock_send[-1]["text"]


def test_telegram_rejects_token(clients_db, mock_send, spawner):
    _begin(clients_db, mock_send)
    with patch("pro_code.activation.get_me", side_effect=TgError("401 Unauthorized")):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    assert "не принял" in mock_send[-1]["text"]
    assert clients_db.get_bot_token(1) is None
    assert spawner == []
    assert activation.is_pending(1) is True


# --- отмена и прочие команды ---------------------------------------------

def test_cancel_resets(clients_db, mock_send):
    _begin(clients_db, mock_send)
    activation.handle("M", 1, 1, "/cancel", clients_db)
    assert activation.is_pending(1) is False
    assert "отменена" in mock_send[-1]["text"]


def test_text_after_cancel_not_absorbed(clients_db, mock_send):
    _begin(clients_db, mock_send)
    activation.handle("M", 1, 1, "/cancel", clients_db)
    assert activation.handle("M", 1, 1, "привет", clients_db) is False


def test_menu_exits_pending(clients_db, mock_send):
    """/menu при ожидании токена выводит из активации."""
    _begin(clients_db, mock_send)
    activation.handle("M", 1, 1, "/menu", clients_db)
    assert activation.is_pending(1) is False


def test_already_activated_shows_status(clients_db, mock_send, spawner):
    _begin(clients_db, mock_send)
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    _start(clients_db, mock_send)
    assert "активен" in mock_send[-1]["text"]
    assert "@client\\_bot" in mock_send[-1]["text"]


def test_replace_token(clients_db, mock_send, spawner):
    _begin(clients_db, mock_send)
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    activation.handle("M", 1, 1, "/replace_token", clients_db)
    assert activation.is_pending(1) is True
    assert "новый токен" in mock_send[-1]["text"]


# --- мои боты ------------------------------------------------------------

def test_my_bots_empty(clients_db, mock_send):
    activation.handle("M", 1, 1, "/my_bots", clients_db)
    assert "нет активированных" in mock_send[-1]["text"]


def test_my_bots_shows_bot(clients_db, mock_send, spawner):
    _begin(clients_db, mock_send)
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    activation.handle("M", 1, 1, "/my_bots", clients_db)
    assert "@client\\_bot" in mock_send[-1]["text"]
    assert "free" in mock_send[-1]["text"]


# --- устойчивость --------------------------------------------------------

def test_spawn_failure_still_saves(clients_db, mock_send):
    """Если поток не поднялся, токен всё равно сохранён."""
    from pro_code import activation as act

    def boom(uid, tok):
        raise RuntimeError("поток не поднялся")

    act.set_spawner(boom)
    try:
        _begin(clients_db, mock_send)
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
    _begin(clients_db, mock_send)
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    assert "ключ" in mock_send[-1]["text"].lower() or "мастер" in mock_send[-1]["text"].lower()
    assert spawner == []


def test_isolated_per_user(clients_db, mock_send, spawner):
    activation.handle("M", 1, 1, "/begin_token", clients_db)
    activation.handle("M", 2, 2, "/begin_token", clients_db)
    assert activation.is_pending(1) is True
    assert activation.is_pending(2) is True
    with patch("pro_code.activation.get_me", return_value=BOT_INFO):
        activation.handle("M", 1, 1, TOKEN, clients_db)
    assert activation.is_pending(1) is False
    assert activation.is_pending(2) is True
