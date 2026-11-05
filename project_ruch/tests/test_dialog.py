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
    # 10 каналов + «Отмена» + «В меню»
    assert len(callbacks) == 12
    assert "cb:/cancel" in callbacks
    assert "cb:/menu" in callbacks


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


# --- кнопка «В меню» -------------------------------------------------------


def test_menu_button_on_channel_screen(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    callbacks = _callbacks(mock_send[-1]["reply_markup"])
    assert "cb:/menu" in callbacks


def test_menu_button_on_content_screen(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    assert "cb:/menu" in _callbacks(mock_send[-1]["reply_markup"])


def test_menu_button_on_confirm_screen(ruch_db, client_with_channel, mock_send):
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    _run("T", 1, 1, "text", client_with_channel, ruch_db)
    assert "cb:/menu" in _callbacks(mock_send[-1]["reply_markup"])


def test_menu_command_clears_draft(ruch_db, client_with_channel, mock_send):
    """«В меню» СБРАСЫВАЕТ черновик, чтобы разблокировать бота из FSM."""
    dialog.start("T", 1, 1, client_with_channel, ruch_db)
    _run("T", 1, 1, "1", client_with_channel, ruch_db)
    _run("T", 1, 1, "/menu", client_with_channel, ruch_db)
    assert ruch_db.get_draft(1) is None
    assert "Состояние сброшено" in mock_send[-1]["text"]


# --- резерв: список и действия ---------------------------------------------


def _add_reserve(user_id: int, text: str = "отложенный") -> int:
    from project_ruch.rezerv_post.rezerv_db import ReserveDb
    db = ReserveDb()
    try:
        return db.add(user_id=user_id, text=text, source="manual")
    finally:
        db.close()


def test_reserve_empty_shows_hint(ruch_db, client_with_channel, mock_send):
    _run("T", 1, 1, "/reserve", client_with_channel, ruch_db)
    assert "Отложенных постов нет" in mock_send[-1]["text"]


def test_reserve_lists_items_with_buttons(ruch_db, client_with_channel, mock_send):
    rid = _add_reserve(1, "пост в резерве")
    _run("T", 1, 1, "/reserve", client_with_channel, ruch_db)
    text = mock_send[-1]["text"]
    assert f"#{rid}" in text
    assert "пост в резерве" in text
    callbacks = _callbacks(mock_send[-1]["reply_markup"])
    assert f"cb:/reserve_pub_{rid}" in callbacks
    assert f"cb:/reserve_del_{rid}" in callbacks


def test_reserve_delete_removes_item(ruch_db, client_with_channel, mock_send):
    rid = _add_reserve(1)
    _run("T", 1, 1, f"/reserve_del_{rid}", client_with_channel, ruch_db)
    assert "удалён из резерва" in mock_send[-1]["text"]
    from project_ruch.rezerv_post.rezerv_db import ReserveDb
    db = ReserveDb()
    try:
        assert db.get(rid) is None
    finally:
        db.close()


def test_reserve_delete_foreign_item_rejected(ruch_db, client_with_channel, mock_send):
    rid = _add_reserve(999)  # чужой
    _run("T", 1, 1, f"/reserve_del_{rid}", client_with_channel, ruch_db)
    assert "не найден" in mock_send[-1]["text"]


def test_reserve_publish_asks_channel(ruch_db, client_with_channel, mock_send):
    rid = _add_reserve(1, "публикуем")
    _run("T", 1, 1, f"/reserve_pub_{rid}", client_with_channel, ruch_db)
    assert "Куда публикуем" in mock_send[-1]["text"]
    assert ruch_db.get_draft(1)["state"] == dialog.STATE_RESERVE_CHANNEL
    callbacks = _callbacks(mock_send[-1]["reply_markup"])
    assert "cb:1" in callbacks


def test_reserve_publish_full_flow(ruch_db, client_with_channel, mock_send):
    rid = _add_reserve(1, "текст из резерва")
    _run("T", 1, 1, f"/reserve_pub_{rid}", client_with_channel, ruch_db)
    with patch("project_ruch.manual_post.send_message",
               return_value={"message_id": 55}):
        _run("T", 1, 1, "1", client_with_channel, ruch_db)
    assert "опубликован" in mock_send[-1]["text"]
    assert ruch_db.get_draft(1) is None
    posts = ruch_db.list_manual_posts(1)
    assert any(p["text"] == "текст из резерва" for p in posts)


def test_reserve_publish_marks_used(ruch_db, client_with_channel, mock_send):
    rid = _add_reserve(1, "одноразовый")
    _run("T", 1, 1, f"/reserve_pub_{rid}", client_with_channel, ruch_db)
    with patch("project_ruch.manual_post.send_message",
               return_value={"message_id": 56}):
        _run("T", 1, 1, "1", client_with_channel, ruch_db)
    from project_ruch.rezerv_post.rezerv_db import ReserveDb
    db = ReserveDb()
    try:
        assert db.get(rid)["status"] == "used"
    finally:
        db.close()


# --- /posts с действиями ---------------------------------------------------


def _make_sent_post(ruch_db, clients_db, text: str = "пост") -> int:
    dialog.start("T", 1, 1, clients_db, ruch_db)
    _run("T", 1, 1, "1", clients_db, ruch_db)
    _run("T", 1, 1, text, clients_db, ruch_db)
    with patch("project_ruch.manual_post.send_message",
               return_value={"message_id": 77}):
        _run("T", 1, 1, "/send", clients_db, ruch_db)
    return ruch_db.list_manual_posts(1)[0]["id"]


def test_posts_list_has_action_buttons(ruch_db, client_with_channel, mock_send):
    pid = _make_sent_post(ruch_db, client_with_channel)
    _run("T", 1, 1, "/posts", client_with_channel, ruch_db)
    callbacks = _callbacks(mock_send[-1]["reply_markup"])
    assert f"cb:/post_del_{pid}" in callbacks
    assert f"cb:/post_resend_{pid}" in callbacks


def test_post_delete_by_id(ruch_db, client_with_channel, mock_send):
    pid = _make_sent_post(ruch_db, client_with_channel)
    with patch("pro_code.tg_api.delete_message", return_value=True):
        _run("T", 1, 1, f"/post_del_{pid}", client_with_channel, ruch_db)
    assert "удалён" in mock_send[-1]["text"]
    assert ruch_db.get_post(pid) is None


def test_post_delete_foreign_rejected(ruch_db, client_with_channel, mock_send):
    _run("T", 1, 1, "/post_del_99999", client_with_channel, ruch_db)
    assert "не найден" in mock_send[-1]["text"]


def test_post_resend_creates_new_post(ruch_db, client_with_channel, mock_send):
    pid = _make_sent_post(ruch_db, client_with_channel, "переотправить меня")
    with patch("project_ruch.manual_post.send_message",
               return_value={"message_id": 88}):
        _run("T", 1, 1, f"/post_resend_{pid}", client_with_channel, ruch_db)
    assert "переотправлен" in mock_send[-1]["text"]
    posts = ruch_db.list_manual_posts(1)
    assert len(posts) == 2
