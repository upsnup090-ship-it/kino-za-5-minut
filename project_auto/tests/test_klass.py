"""Тесты классификатора файлов: kino/klass.py."""
from __future__ import annotations

from kino.klass import FileClass, classify, is_original


def test_venv_excluded():
    assert classify("/workspace/project_auto/venv/bin/python") == FileClass.EXCLUDED


def test_mcp_venv_excluded():
    assert classify("/workspace/project_auto/mcp/venv/bin/python") == FileClass.EXCLUDED


def test_logs_deriv():
    assert classify("/workspace/project_auto/logs/kino.log") == FileClass.DERIV


def test_feed_cache_deriv():
    assert classify("/workspace/project_auto/data/feed_cache/reddit.xml") == FileClass.DERIV


def test_kino_db_is_state():
    assert classify("/workspace/project_auto/data/kino.db") == FileClass.STATE


def test_state_json_is_state():
    assert classify("/workspace/project_auto/data/state.json") == FileClass.STATE


def test_env_is_secret():
    assert classify("/workspace/project_auto/.env") == FileClass.SECRET
    assert classify("/workspace/project_auto/.env.test") == FileClass.SECRET


def test_graph_token_is_secret():
    assert classify("/workspace/project_auto/data/graph_token.txt") == FileClass.SECRET


def test_py_file_is_original():
    assert classify("/workspace/project_auto/kino/caption.py") == FileClass.ORIG
    assert is_original("/workspace/project_auto/config/settings.yaml")


def test_pyc_is_excluded():
    assert classify(
        "/workspace/project_auto/kino/__pycache__/foo.cpython-312.pyc"
    ) == FileClass.EXCLUDED


def test_outside_root_is_unknown():
    assert classify("/workspace/other/file.txt") == FileClass.UNKNOWN