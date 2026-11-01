"""Тесты классификатора файлов: kino/klass.py."""
from __future__ import annotations

from kino.klass import FileClass, classify, is_original


def test_venv_excluded():
    assert classify("/workspace/project/venv/bin/python") == FileClass.EXCLUDED


def test_mcp_venv_excluded():
    assert classify("/workspace/project/mcp/venv/bin/python") == FileClass.EXCLUDED


def test_logs_deriv():
    assert classify("/workspace/project/logs/kino.log") == FileClass.DERIV


def test_feed_cache_deriv():
    assert classify("/workspace/project/data/feed_cache/reddit.xml") == FileClass.DERIV


def test_kino_db_is_state():
    assert classify("/workspace/project/data/kino.db") == FileClass.STATE


def test_state_json_is_state():
    assert classify("/workspace/project/data/state.json") == FileClass.STATE


def test_env_is_secret():
    assert classify("/workspace/project/.env") == FileClass.SECRET
    assert classify("/workspace/project/.env.test") == FileClass.SECRET


def test_graph_token_is_secret():
    assert classify("/workspace/project/data/graph_token.txt") == FileClass.SECRET


def test_py_file_is_original():
    assert classify("/workspace/project/kino/caption.py") == FileClass.ORIG
    assert is_original("/workspace/project/config/settings.yaml")


def test_pyc_is_excluded():
    assert classify(
        "/workspace/project/kino/__pycache__/foo.cpython-312.pyc"
    ) == FileClass.EXCLUDED


def test_outside_root_is_unknown():
    assert classify("/workspace/other/file.txt") == FileClass.UNKNOWN