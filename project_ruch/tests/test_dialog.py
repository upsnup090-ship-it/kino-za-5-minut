"""Тесты FSM ручного постинга."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from project_ruch import dialog


def _run(token, chat_id, user_id, text, clients_db, ruch_db, raw=None):
    return dialog.handle(token, chat_id, user_id, text, clients_db, ruch_db, raw_message=raw)


def test_start_without_channels(ruch_db, clients_db, mock_send):
    clients_db.upsert_client(1, plan="free")
    dialog.start("T", 1, 1, clients_db, ruch_db)
    assert "нет каналов" in mock_send[-1]["text"]
    assert ruch_db.get_draft(1) is None


def test_start_lists_channels(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    assert "Test Channel" in mock_send[-1]["text"]
    assert ruch_db.get_draft(1)["state"] == dialog.STATE_CHANNEL


def test_full_flow_send(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    assert _run("T", 1, 1, "1", client_with_channel, ruch_db) is True
    assert ruch_db.get_draft(1)["state"] == dialog.STATE_CONTENT
    assert ruch_db.get_draft(1)["channel_chat_id"] == -100123

    _run("T", 1, 1, "Hello world", client_with_channel, ruch_db)
    assert ruch_db.get_draft(1)["state"] == dialog.STATE_CONFIRM

    with patch("project_ruch.manual_post.send_message",
               return_value={"message_id": 42}):
        _run("T", 1, 1, "/send", client_with_channel, ruch_db)

    assert ruch_db.get_draft(1) is None
    posts = ruch_db.list_manual_posts(1)
    assert len(posts) == 1
    assert posts[0]["status"] == "sent"
    assert posts[0]["text"] == "Hello world"


def test_channel_bad_number(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "99", client_with_channel, ruch_db)
    assert ruch_db.get_draft(1)["state"] == dialog.STATE_CHANNEL
    assert "Нет такого номера" in mock_send[-1]["text"]


def test_channel_not_a_number(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "abc", client_with_channel, ruch_db)
    assert ruch_db.get_draft(1)["state"] == dialog.STATE_CHANNEL


def test_empty_text_rejected(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    _run("T", 1, 1, "   ", client_with_channel, ruch_db)
    assert ruch_db.get_draft(1)["state"] == dialog.STATE_CONTENT
    assert "Пустой текст" in mock_send[-1]["text"]


def test_photo_message_sets_media(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    raw = {"photo": [{"file_id": "PH1"}], "caption": "подпись"}
    _run("T", 1, 1, "", client_with_channel, ruch_db, raw=raw)
    d = ruch_db.get_draft(1)
    assert d["state"] == dialog.STATE_CONFIRM
    assert d["media_type"] == "photo"
    assert d["media_path"] == "PH1"
    assert d["text"] == "подпись"


def test_document_message(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    raw = {"document": {"file_id": "DOC1"}, "caption": "файл"}
    _run("T", 1, 1, "", client_with_channel, ruch_db, raw=raw)
    assert ruch_db.get_draft(1)["media_type"] == "document"
    assert ruch_db.get_draft(1)["media_path"] == "DOC1"


def test_video_message(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    raw = {"video": {"file_id": "VID1"}, "caption": "видео"}
    _run("T", 1, 1, "", client_with_channel, ruch_db, raw=raw)
    assert ruch_db.get_draft(1)["media_type"] == "video"


def test_cancel_clears_draft(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "/cancel", client_with_channel, ruch_db)
    assert ruch_db.get_draft(1) is None


def test_edit_returns_to_content(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    _run("T", 1, 1, "first", client_with_channel, ruch_db)
    _run("T", 1, 1, "/edit", client_with_channel, ruch_db)
    assert ruch_db.get_draft(1)["state"] == dialog.STATE_CONTENT
    assert ruch_db.get_draft(1)["text"] == ""


def test_send_failure_keeps_draft(ruch_db, client_with_channel, mock_send):
    from pro_code.tg_api import TgError

    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    _run("T", 1, 1, "text", client_with_channel, ruch_db)
    with patch("project_ruch.manual_post.send_message",
               side_effect=TgError("403")):
        _run("T", 1, 1, "/send", client_with_channel, ruch_db)
    assert ruch_db.get_draft(1) is not None
    assert "Не отправлено" in mock_send[-1]["text"]


def test_posts_listing(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    _run("T", 1, 1, "hello", client_with_channel, ruch_db)
    with patch("project_ruch.manual_post.send_message",
               return_value={"message_id": 1}):
        _run("T", 1, 1, "/send", client_with_channel, ruch_db)
    _run("T", 1, 1, "/posts", client_with_channel, ruch_db)
    assert "hello" in mock_send[-1]["text"]


def test_idle_returns_false(ruch_db, client_with_channel, mock_send):
    assert _run("T", 1, 1, "random text", client_with_channel, ruch_db) is False


def test_del_last(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    _run("T", 1, 1, "hello", client_with_channel, ruch_db)
    with patch("project_ruch.manual_post.send_message",
               return_value={"message_id": 7}):
        _run("T", 1, 1, "/send", client_with_channel, ruch_db)
    with patch("pro_code.tg_api.delete_message", return_value=True):
        _run("T", 1, 1, "/del_last", client_with_channel, ruch_db)
    assert "удалён" in mock_send[-1]["text"]
    assert ruch_db.list_manual_posts(1) == []


# --- кнопки (inline keyboard) ---------------------------------------------


def _buttons(markup):
    """Плоский список подписей кнопок из reply_markup."""
    if not markup:
        return []
    return [b["text"] for row in markup["inline_keyboard"] for b in row]


def _callbacks(markup):
    if not markup:
        return []
    return [b["callback_data"] for row in markup["inline_keyboard"] for b in row]


def test_start_sends_channel_keyboard(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    markup = mock_send[-1].get("reply_markup")
    assert markup is not None
    labels = _buttons(markup)
    assert any("Test Channel" in b for b in labels)
    assert "❌ Отмена" in labels
    assert "cb:1" in _callbacks(markup)
    assert "cb:/cancel" in _callbacks(markup)


def test_channel_keyboard_limits_to_ten(ruch_db, clients_db, mock_send):
    clients_db.upsert_client(1, plan="free")
    for i in range(15):
        clients_db.add_channel(1, -100000 - i, f"ch{i}", "channel", purpose="manual")
    dialog.start("T", 1, 1, clients_db, ruch_db)
    callbacks = _callbacks(mock_send[-1]["reply_markup"])
    # 10 каналов + отмена
    assert len(callbacks) == 11


def test_channel_button_callback_selects(ruch_db, client_with_channel, mock_send):
    """callback_data cb:1 → dispatcher отдаёт '1' → канал выбран."""
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    assert ruch_db.get_draft(1)["channel_chat_id"] == -100123


def test_content_step_has_cancel_button(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    assert "❌ Отмена" in _buttons(mock_send[-1]["reply_markup"])


def test_confirm_keyboard_has_four_actions(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    _run("T", 1, 1, "text", client_with_channel, ruch_db)
    labels = _buttons(mock_send[-1]["reply_markup"])
    assert "✅ Отправить" in labels
    assert "✏️ Изменить" in labels
    assert "⏳ В резерв" in labels
    assert "❌ Отмена" in labels


def test_after_send_keyboard_offers_new_post(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    _run("T", 1, 1, "text", client_with_channel, ruch_db)
    with patch("project_ruch.manual_post.send_message",
               return_value={"message_id": 1}):
        _run("T", 1, 1, "/send", client_with_channel, ruch_db)
    labels = _buttons(mock_send[-1]["reply_markup"])
    assert "✍️ Новый пост" in labels
    assert "📋 Мои посты" in labels


def test_send_failure_keyboard_offers_retry(ruch_db, client_with_channel, mock_send):
    from pro_code.tg_api import TgError

    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    _run("T", 1, 1, "text", client_with_channel, ruch_db)
    with patch("project_ruch.manual_post.send_message", side_effect=TgError("403")):
        _run("T", 1, 1, "/send", client_with_channel, ruch_db)
    labels = _buttons(mock_send[-1]["reply_markup"])
    assert "🔁 Повторить" in labels
    assert "❌ Отмена" in labels


def test_posts_keyboard(ruch_db, client_with_channel, mock_send):
    _run("T", 1, 1, "/posts", client_with_channel, ruch_db)
    labels = _buttons(mock_send[-1]["reply_markup"])
    assert "✍️ Новый пост" in labels


def test_new_post_button_restarts(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    _run("T", 1, 1, "/post", client_with_channel, ruch_db)
    assert ruch_db.get_draft(1)["state"] == dialog.STATE_CHANNEL


def test_bad_channel_number_resends_keyboard(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "99", client_with_channel, ruch_db)
    assert mock_send[-1].get("reply_markup") is not None


def test_callback_data_prefix(ruch_db, client_with_channel, mock_send):
    """Все callback_data начинаются с cb: — иначе dispatcher их не узнает."""
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    for data in _callbacks(mock_send[-1]["reply_markup"]):
        assert data.startswith(dialog.CB_PREFIX)
