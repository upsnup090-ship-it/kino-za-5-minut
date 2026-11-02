"""Тесты публикации ручного поста (Bot API замокан)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pro_code.tg_api import TgError
from project_ruch import manual_post as mp


def test_create_post_draft(ruch_db):
    res = mp.create_post(ruch_db, 1, -100123, "hello")
    assert res["status"] == "draft"
    post = ruch_db.get_post(res["id"])
    assert post["text"] == "hello"
    assert post["status"] == "draft"


def test_publish_text_success(ruch_db):
    pid = mp.create_post(ruch_db, 1, -100123, "hello")["id"]
    with patch("project_ruch.manual_post.send_message",
               return_value={"message_id": 777}) as m:
        res = mp.publish_post(ruch_db, pid, "TOKEN")
    assert res["ok"] is True
    assert res["message_id"] == 777
    assert ruch_db.get_post(pid)["status"] == "sent"
    assert ruch_db.get_message_id(pid) == 777
    m.assert_called_once()
    args, kwargs = m.call_args
    assert args[1] == -100123
    assert args[2] == "hello"


def test_publish_passes_parse_mode(ruch_db):
    pid = mp.create_post(ruch_db, 1, -100123, "<b>x</b>", parse_mode="HTML")["id"]
    with patch("project_ruch.manual_post.send_message",
               return_value={"message_id": 1}) as m:
        mp.publish_post(ruch_db, pid, "TOKEN")
    assert m.call_args.kwargs.get("parse_mode") == "HTML"


def test_publish_passes_reply_to(ruch_db):
    pid = mp.create_post(ruch_db, 1, -100123, "x", reply_to_msg_id=42)["id"]
    with patch("project_ruch.manual_post.send_message",
               return_value={"message_id": 1}) as m:
        mp.publish_post(ruch_db, pid, "TOKEN")
    assert m.call_args.kwargs.get("reply_to_message_id") == 42


def test_publish_already_sent_no_duplicate(ruch_db):
    pid = mp.create_post(ruch_db, 1, -100123, "hello")["id"]
    with patch("project_ruch.manual_post.send_message",
               return_value={"message_id": 777}):
        mp.publish_post(ruch_db, pid, "TOKEN")
    with patch("project_ruch.manual_post.send_message") as m:
        res = mp.publish_post(ruch_db, pid, "TOKEN")
    assert res["ok"] is True
    assert res.get("already_sent") is True
    assert m.call_count == 0


def test_publish_tg_error_marks_failed(ruch_db):
    pid = mp.create_post(ruch_db, 1, -100123, "hello")["id"]
    with patch("project_ruch.manual_post.send_message",
               side_effect=TgError("403 Forbidden")):
        res = mp.publish_post(ruch_db, pid, "TOKEN")
    assert res["ok"] is False
    assert "403" in res["error"]
    assert ruch_db.get_post(pid)["status"] == "failed"


def test_publish_missing_post(ruch_db):
    res = mp.publish_post(ruch_db, 999, "TOKEN")
    assert res["ok"] is False
    assert "не найден" in res["error"]


def test_publish_text_over_limit(ruch_db):
    pid = mp.create_post(ruch_db, 1, -100123, "x" * (mp.TEXT_LIMIT + 1))["id"]
    with patch("project_ruch.manual_post.send_message") as m:
        res = mp.publish_post(ruch_db, pid, "TOKEN")
    assert res["ok"] is False
    assert "лимит" in res["error"]
    assert m.call_count == 0
    assert ruch_db.get_post(pid)["status"] == "failed"


def test_publish_media_missing_file(ruch_db):
    pid = mp.create_post(
        ruch_db, 1, -100123, "cap", media_type="photo",
        media_path="/nonexistent.jpg",
    )["id"]
    res = mp.publish_post(ruch_db, pid, "TOKEN")
    assert res["ok"] is False
    assert ruch_db.get_post(pid)["status"] == "failed"


def test_publish_media_caches_file_id(ruch_db):
    pid = mp.create_post(
        ruch_db, 1, -100123, "cap", media_type="photo",
        media_path="https://example.com/a.jpg",
    )["id"]
    with patch("project_ruch.manual_post.send_media",
               return_value={"ok": True, "message_id": 5, "file_id": "FILE123"}):
        res = mp.publish_post(ruch_db, pid, "TOKEN")
    assert res["ok"] is True
    assert res["file_id"] == "FILE123"
    assert ruch_db.get_post(pid)["media_path"] == "FILE123"


def test_publish_media_error(ruch_db):
    pid = mp.create_post(
        ruch_db, 1, -100123, "cap", media_type="photo",
        media_path="https://example.com/a.jpg",
    )["id"]
    with patch("project_ruch.manual_post.send_media",
               return_value={"ok": False, "error": "boom"}):
        res = mp.publish_post(ruch_db, pid, "TOKEN")
    assert res["ok"] is False
    assert ruch_db.get_post(pid)["status"] == "failed"


def test_delete_last_post(ruch_db):
    pid = mp.create_post(ruch_db, 1, -100123, "hello")["id"]
    with patch("project_ruch.manual_post.send_message",
               return_value={"message_id": 777}):
        mp.publish_post(ruch_db, pid, "TOKEN")
    with patch("pro_code.tg_api.delete_message", return_value=True):
        res = mp.delete_last_post(ruch_db, 1, "TOKEN")
    assert res["ok"] is True
    assert res["message_id"] == 777
    assert ruch_db.get_post(pid) is None


def test_delete_last_post_empty(ruch_db):
    res = mp.delete_last_post(ruch_db, 1, "TOKEN")
    assert res["ok"] is False
    assert "нет отправленных" in res["error"]


def test_list_posts(ruch_db):
    mp.create_post(ruch_db, 1, -100123, "a")
    mp.create_post(ruch_db, 1, -100123, "b")
    assert len(mp.list_posts(ruch_db, 1)) == 2
