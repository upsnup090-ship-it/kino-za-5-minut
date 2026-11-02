"""Тесты схемы и методов ruch.db."""

from __future__ import annotations

import pytest

from project_ruch.ruch_db import RuchDb


def test_schema_has_all_tables(ruch_db):
    names = {
        r["name"]
        for r in ruch_db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "repeating_messages", "manual_posts", "post_stats",
        "user_backups", "bindings", "post_drafts",
    } <= names


def test_save_binding_works(ruch_db):
    """Регресс: bindings не было в схеме, save_binding падал OperationalError."""
    ruch_db.save_binding(1, -100123, "channel")
    bindings = ruch_db.list_bindings(1)
    assert len(bindings) == 1
    assert bindings[0]["chat_id"] == -100123
    assert bindings[0]["type"] == "channel"


def test_save_binding_idempotent(ruch_db):
    ruch_db.save_binding(1, -100123, "channel")
    ruch_db.save_binding(1, -100123, "channel")
    assert len(ruch_db.list_bindings(1)) == 1


def test_save_binding_rejects_bad_type(ruch_db):
    with pytest.raises(Exception):
        ruch_db.save_binding(1, -100123, "bogus")


def test_mark_sent_writes_message_id(ruch_db):
    """Регресс: message_id принимался, но не писался."""
    pid = ruch_db.add_manual_post(1, -100123, "hello")
    ruch_db.mark_sent(pid, 555)
    post = ruch_db.get_post(pid)
    assert post["status"] == "sent"
    assert post["sent_at"]
    assert ruch_db.get_message_id(pid) == 555


def test_mark_sent_without_message_id(ruch_db):
    pid = ruch_db.add_manual_post(1, -100123, "hello")
    ruch_db.mark_sent(pid)
    assert ruch_db.get_post(pid)["status"] == "sent"
    assert ruch_db.get_message_id(pid) is None


def test_mark_failed(ruch_db):
    pid = ruch_db.add_manual_post(1, -100123, "hello")
    ruch_db.mark_failed(pid)
    assert ruch_db.get_post(pid)["status"] == "failed"


def test_get_post_missing(ruch_db):
    assert ruch_db.get_post(999) is None


def test_delete_post(ruch_db):
    pid = ruch_db.add_manual_post(1, -100123, "hello")
    assert ruch_db.delete_post(pid) is True
    assert ruch_db.get_post(pid) is None
    assert ruch_db.delete_post(pid) is False


def test_draft_lifecycle(ruch_db):
    assert ruch_db.get_draft(1) is None
    ruch_db.save_draft(1, state="await_channel")
    assert ruch_db.get_draft(1)["state"] == "await_channel"
    ruch_db.save_draft(1, channel_chat_id=-100123)
    d = ruch_db.get_draft(1)
    assert d["state"] == "await_channel"
    assert d["channel_chat_id"] == -100123
    ruch_db.clear_draft(1)
    assert ruch_db.get_draft(1) is None


def test_draft_upsert_single_row(ruch_db):
    ruch_db.save_draft(1, state="a")
    ruch_db.save_draft(1, state="b")
    rows = ruch_db.conn.execute(
        "SELECT COUNT(*) AS n FROM post_drafts WHERE user_id = 1"
    ).fetchone()
    assert rows["n"] == 1


def test_str_path_accepted(tmp_path):
    """Регресс: RuchDb('path') падал на path.parent у строки."""
    db = RuchDb(str(tmp_path / "x.db"))
    try:
        assert db.get_draft(1) is None
    finally:
        db.close()


def test_get_last_posts_only_sent(ruch_db):
    a = ruch_db.add_manual_post(1, -100123, "sent one")
    ruch_db.mark_sent(a, 1)
    ruch_db.add_manual_post(1, -100123, "draft one")
    posts = ruch_db.get_last_posts(1, n=5)
    assert len(posts) == 1
    assert posts[0]["text"] == "sent one"
