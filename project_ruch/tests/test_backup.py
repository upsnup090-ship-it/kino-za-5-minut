"""Тесты пользовательского бэкапа (CSV/TXT, 2 последних)."""

from __future__ import annotations

import pytest

from project_ruch import backup as bk
from project_ruch import manual_post as mp


@pytest.fixture
def backup_dir(tmp_path):
    """Временный каталог бэкапов, передаётся явно."""
    d = tmp_path / "user_backups"
    d.mkdir()
    return d


def test_generate_csv(ruch_db, backup_dir):
    pid = mp.create_post(ruch_db, 1, -100123, "hello")["id"]
    ruch_db.mark_sent(pid, 1)
    bindings = [{"chat_id": -100123, "type": "channel", "role": "member",
                 "added_at": "2026-01-01"}]
    name = bk.generate_user_backup(ruch_db, 1, bindings, fmt="csv",
                                   backup_dir=backup_dir)
    assert name.endswith(".csv")
    content = (backup_dir / name).read_text(encoding="utf-8")
    assert "chat_id" in content
    assert "hello" in content
    assert "total_posts" in content


def test_generate_txt(ruch_db, backup_dir):
    pid = mp.create_post(ruch_db, 1, -100123, "hello")["id"]
    ruch_db.mark_sent(pid, 1)
    name = bk.generate_user_backup(ruch_db, 1, [], fmt="txt",
                                   backup_dir=backup_dir)
    assert name.endswith(".txt")
    content = (backup_dir / name).read_text(encoding="utf-8")
    assert "Привязки" in content
    assert "Метрика" in content


def test_keeps_two_backups(ruch_db, backup_dir):
    for _ in range(4):
        bk.generate_user_backup(ruch_db, 1, [], fmt="csv", backup_dir=backup_dir)
    files = list(backup_dir.glob("backup_user_1_*.csv"))
    assert len(files) == 2
    assert len(ruch_db.list_backups(1)) == 2


def test_backup_meta_recorded(ruch_db, backup_dir):
    name = bk.generate_user_backup(ruch_db, 1, [], fmt="csv",
                                   backup_dir=backup_dir)
    metas = ruch_db.list_backups(1)
    assert len(metas) == 1
    assert metas[0]["filename"] == name
    assert metas[0]["format"] == "csv"
    assert metas[0]["size_bytes"] > 0


def test_unknown_format_falls_back_to_csv(ruch_db, backup_dir):
    name = bk.generate_user_backup(ruch_db, 1, [], fmt="xml",
                                   backup_dir=backup_dir)
    assert name.endswith(".csv")


def test_last_five_posts_only(ruch_db, backup_dir):
    for i in range(7):
        pid = mp.create_post(ruch_db, 1, -100123, f"post {i}")["id"]
        ruch_db.mark_sent(pid, i + 1)
    name = bk.generate_user_backup(ruch_db, 1, [], fmt="txt",
                                   backup_dir=backup_dir)
    content = (backup_dir / name).read_text(encoding="utf-8")
    assert content.count("[#") == 5


def test_backup_isolated_per_user(ruch_db, backup_dir):
    pid = mp.create_post(ruch_db, 1, -100123, "user1 post")["id"]
    ruch_db.mark_sent(pid, 1)
    name = bk.generate_user_backup(ruch_db, 2, [], fmt="txt",
                                   backup_dir=backup_dir)
    content = (backup_dir / name).read_text(encoding="utf-8")
    assert "user1 post" not in content
