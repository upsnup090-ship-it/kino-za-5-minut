"""Тесты моста ручного постинга → rezerv_post."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from project_ruch import reserve as rv
from rezerv_post.rezerv_db import ReserveDb


@pytest.fixture
def reserve_db(tmp_path):
    """Своя БД резерва на каждый тест, подменяет DB_PATH модуля."""
    path = tmp_path / "rezerv.db"
    with patch.object(ReserveDb, "__init__", _init_with(path)):
        yield path


def _init_with(path):
    """Фабрика __init__ для ReserveDb с фиксированным путём."""
    original = ReserveDb.__init__

    def _init(self, db_path=None):
        original(self, path)

    return _init


def test_add_to_reserve(ruch_db, reserve_db):
    rid = rv.add_to_reserve(ruch_db, 1, -100123, "reserved")
    assert rid > 0
    assert rv.count_pending(1) == 1


def test_list_pending(ruch_db, reserve_db):
    rv.add_to_reserve(ruch_db, 1, -100123, "a")
    rv.add_to_reserve(ruch_db, 1, -100123, "b")
    pending = rv.list_pending(1)
    assert len(pending) == 2
    assert pending[0]["text"] == "a"


def test_publish_next(ruch_db, reserve_db):
    rv.add_to_reserve(ruch_db, 1, -100123, "reserved post")
    with patch("project_ruch.manual_post.send_message",
               return_value={"message_id": 99}):
        res = rv.publish_next(ruch_db, 1, "T", -100123)
    assert res["ok"] is True
    assert res["message_id"] == 99
    assert res["reserve_id"] > 0
    assert rv.count_pending(1) == 0
    posts = ruch_db.list_manual_posts(1)
    assert len(posts) == 1
    assert posts[0]["status"] == "sent"
    assert posts[0]["text"] == "reserved post"


def test_publish_next_empty(ruch_db, reserve_db):
    with patch("project_ruch.manual_post.send_message") as m:
        res = rv.publish_next(ruch_db, 1, "T", -100123)
    assert res["ok"] is False
    assert "резерв пуст" in res["error"]
    assert m.call_count == 0


def test_publish_next_failure_marks_failed(ruch_db, reserve_db):
    from pro_code.tg_api import TgError

    rv.add_to_reserve(ruch_db, 1, -100123, "reserved")
    with patch("project_ruch.manual_post.send_message",
               side_effect=TgError("403")):
        res = rv.publish_next(ruch_db, 1, "T", -100123)
    assert res["ok"] is False
    posts = ruch_db.list_manual_posts(1)
    assert posts[0]["status"] == "failed"


def test_reserve_isolated_per_user(ruch_db, reserve_db):
    rv.add_to_reserve(ruch_db, 1, -100123, "user1")
    rv.add_to_reserve(ruch_db, 2, -100456, "user2")
    assert rv.count_pending(1) == 1
    assert rv.count_pending(2) == 1
    assert rv.list_pending(1)[0]["text"] == "user1"


def test_purge_expired(ruch_db, reserve_db):
    rv.add_to_reserve(ruch_db, 1, -100123, "x")
    with patch("project_ruch.manual_post.send_message",
               return_value={"message_id": 1}):
        rv.publish_next(ruch_db, 1, "T", -100123)
    removed = rv.purge_expired()
    assert removed >= 1
