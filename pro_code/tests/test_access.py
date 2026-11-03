"""Регресс на P0-2: посторонний не управляет ботом клиента.

В аудите сценарий проходил целиком: человек с чужим id открывал бота
клиента, выбирал его канал и публиковал в него свой текст от имени клиента.
Здесь проверяем, что после правки гейт срабатывает до любых записей в БД.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pro_code import access
from pro_code.clients_db import ClientsDb
from pro_code.config import ClientBot, Config
from pro_code.dispatcher import _dispatch_master, _dispatch_update
from project_ruch.ruch_db import RuchDb

OWNER = 777
STRANGER = 999
ADMIN = 1


@pytest.fixture
def setup(tmp_path):
    """Бот клиента OWNER с одним каналом, пригодным для ручного постинга."""
    clients = ClientsDb(tmp_path / "clients.db")
    ruch = RuchDb(tmp_path / "ruch.db")
    clients.upsert_client(OWNER, plan="auto")
    clients.add_channel(OWNER, -1001234, "Канал клиента", "channel", purpose="both")
    cfg = Config(
        admin_chat_id=ADMIN,
        clients=[ClientBot(user_id=OWNER, token="1:X", plan="auto")],
    )
    yield cfg, clients, ruch
    clients.close_all()
    ruch.close_all()


def _msg(sender: int, text: str, chat_id: int | None = None) -> dict:
    return {
        "message": {
            "chat": {"id": sender if chat_id is None else chat_id, "type": "private"},
            "from": {"id": sender},
            "text": text,
        }
    }


def _cb(sender: int, data: str, chat_id: int | None = None) -> dict:
    return {
        "callback_query": {
            "id": "cb1",
            "from": {"id": sender},
            "message": {"chat": {"id": sender if chat_id is None else chat_id}},
            "data": data,
        }
    }


def test_actor_of_reads_sender():
    assert access.actor_of(_msg(STRANGER, "/start")) == STRANGER
    assert access.actor_of(_cb(STRANGER, "cb:/post")) == STRANGER
    assert access.actor_of({}) is None


def test_is_owner_only_for_owner():
    assert access.is_owner(OWNER, OWNER) is True
    assert access.is_owner(STRANGER, OWNER) is False
    assert access.is_owner(None, OWNER) is False


def test_stranger_cannot_open_post_dialog(setup):
    """Главный сценарий аудита: /post от постороннего — отказ, БД чиста."""
    cfg, clients, ruch = setup
    sent: list[str] = []

    with patch("pro_code.dispatcher.send_message",
               side_effect=lambda t, c, txt, **k: sent.append(txt) or {"message_id": 1}), \
         patch("pro_code.dispatcher.answer_callback"):
        _dispatch_update(cfg, clients, ruch, "1:X", OWNER, "auto",
                         _msg(STRANGER, "/post"))

    assert ruch.get_draft(OWNER) is None, "посторонний создал черновик!"
    assert any("не для тебя" in s for s in sent), "нет отказа"


def test_stranger_publish_not_called(setup):
    """Посторонний не может дойти до публикации."""
    cfg, clients, ruch = setup
    published = []

    with patch("pro_code.dispatcher.send_message", return_value={"message_id": 1}), \
         patch("pro_code.dispatcher.answer_callback"), \
         patch("project_ruch.manual_post.publish_post",
               side_effect=lambda *a, **k: published.append(a) or {"ok": True}):
        for txt in ("/post", "1", "Взлом", "/send"):
            _dispatch_update(cfg, clients, ruch, "1:X", OWNER, "auto",
                             _msg(STRANGER, txt))

    assert published == [], "посторонний опубликовал пост!"
    assert ruch.get_draft(OWNER) is None


def test_stranger_callback_ignored(setup):
    """Кнопки ручного постинга от постороннего не выполняются."""
    cfg, clients, ruch = setup
    with patch("pro_code.dispatcher.send_message", return_value={"message_id": 1}), \
         patch("pro_code.dispatcher.answer_callback"):
        _dispatch_update(cfg, clients, ruch, "1:X", OWNER, "auto",
                         _cb(STRANGER, "cb:/post"))
    assert ruch.get_draft(OWNER) is None


def test_owner_passes_gate(setup):
    """Владелец бота работает как раньше: /post открывает диалог."""
    cfg, clients, ruch = setup
    with patch("pro_code.dispatcher.send_message", return_value={"message_id": 1}), \
         patch("pro_code.dispatcher.answer_callback"):
        _dispatch_update(cfg, clients, ruch, "1:X", OWNER, "auto",
                         _msg(OWNER, "/post"))
    assert ruch.get_draft(OWNER) is not None


def test_missing_sender_denied(setup):
    """Апдейт без from — тоже отказ (лучше не выполнить, чем выполнить чужое)."""
    cfg, clients, ruch = setup
    with patch("pro_code.dispatcher.send_message", return_value={"message_id": 1}), \
         patch("pro_code.dispatcher.answer_callback"):
        _dispatch_update(cfg, clients, ruch, "1:X", OWNER, "free",
                         {"message": {"chat": {"id": STRANGER}, "text": "/post"}})
    assert ruch.get_draft(OWNER) is None


def test_master_bot_allows_activation_for_all(setup):
    """Активация своего бота на мастер-боте доступна любому клиенту."""
    cfg, clients, ruch = setup
    handled: list[tuple] = []

    with patch("pro_code.dispatcher.activation.handle",
               side_effect=lambda *a, **k: handled.append(a) or True), \
         patch("pro_code.dispatcher.send_message", return_value={"message_id": 1}), \
         patch("pro_code.dispatcher.answer_callback"):
        _dispatch_master(cfg, clients, ruch, "M:X", _msg(STRANGER, "/start"))

    assert handled, "активация не дошла"
    assert handled[0][2] == STRANGER, "user_id должен быть from.id клиента"


def test_master_bot_denies_stranger_commands(setup):
    """Не-админ на мастер-боте не получает доступ к RootA/меню."""
    cfg, clients, ruch = setup
    root_called: list = []

    with patch("pro_code.dispatcher.activation.handle", return_value=False), \
         patch("pro_code.dispatcher.handle_message",
               side_effect=lambda *a, **k: root_called.append(a)), \
         patch("pro_code.dispatcher.send_message", return_value={"message_id": 1}), \
         patch("pro_code.dispatcher.answer_callback"):
        _dispatch_master(cfg, clients, ruch, "M:X", _msg(STRANGER, "/clients"))

    assert root_called == [], "посторонний дошёл до RootA"


def test_master_bot_allows_admin_commands(setup):
    """Админ на мастер-боте — обычный путь."""
    cfg, clients, ruch = setup
    root_called: list = []

    with patch("pro_code.dispatcher.activation.handle", return_value=False), \
         patch("pro_code.dispatcher.handle_message",
               side_effect=lambda *a, **k: root_called.append(a)), \
         patch("pro_code.dispatcher.send_message", return_value={"message_id": 1}), \
         patch("pro_code.dispatcher.answer_callback"):
        _dispatch_master(cfg, clients, ruch, "M:X", _msg(ADMIN, "/clients"))

    assert root_called, "админ не дошёл до роутера"


def test_master_callback_denies_stranger(setup):
    """Кнопка мастер-бота (не активация) от постороннего — отказ."""
    cfg, clients, ruch = setup
    with patch("pro_code.dispatcher.send_message", return_value={"message_id": 1}), \
         patch("pro_code.dispatcher.answer_callback"), \
         patch("pro_code.dispatcher.activation.handle") as act:
        _dispatch_master(cfg, clients, ruch, "M:X", _cb(STRANGER, "cb:/backup"))
    act.assert_not_called()
