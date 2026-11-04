"""Дедупликация опубликованных постов.

До этого хэш поста считался в publish(), логировался и выбрасывался —
комментарий в коде честно писал «для простоты пока просто логируем».
Дедупликации не было: падение между send и save_state и следующий прогон
мог повторно отправить тот же пост в канал.

Теперь хэш проверяется до отправки (publish(seen_hashes=...)) и пишется
в state.recent_post_hashes после успеха.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from kino.config import Config, Settings
from kino.publish import _hash_post_content, publish
from kino.state import State, load_state, save_state


def _make_config() -> Config:
    return Config(
        sources=[],
        weights={},
        bad=[],
        sad=[],
        settings=Settings(),
        llm_base_url="",
        llm_api_key="",
        draft_model="",
        judge_model="",
        bot_token="bot-token",
        channel_chat_id="-100123",
        admin_chat_id="-100456",
    )


# --- publish: проверка seen_hashes -----------------------------------------


def test_publish_skips_already_seen_post():
    """Пост с известным хэшем не отправляется повторно."""
    cfg = _make_config()
    caption, image, kind, link = "текст поста", "https://e.com/p.jpg", "photo", ""
    seen = {_hash_post_content(caption, image, kind, link)}

    with patch("kino.publish.send_photo") as mock_photo, \
         patch("kino.publish.send_text") as mock_text:
        assert publish(
            cfg, caption, image, dry_run=False, kind=kind,
            link_url=link, seen_hashes=seen,
        ) is False

    mock_photo.assert_not_called()
    mock_text.assert_not_called()


def test_publish_sends_when_hash_unknown():
    cfg = _make_config()
    with patch("kino.publish.send_photo", return_value=True):
        assert publish(
            cfg, "новый пост", "https://e.com/p.jpg", dry_run=False,
            kind="photo", seen_hashes=set(),
        ) is True


def test_publish_without_seen_hashes_still_works():
    """Обратная совместимость: вызов без seen_hashes не падает."""
    cfg = _make_config()
    with patch("kino.publish.send_photo", return_value=True):
        assert publish(
            cfg, "пост", "https://e.com/p.jpg", dry_run=False, kind="photo",
        ) is True


def test_publish_different_variation_is_not_skipped():
    """Тот же текст, но другая вариация — это другой пост, не дубль."""
    cfg = _make_config()
    caption, image = "текст", "https://e.com/p.jpg"
    seen = {_hash_post_content(caption, image, "photo", "")}
    with patch("kino.publish.send_text", return_value=True):
        assert publish(
            cfg, caption, "", dry_run=False, kind="text", seen_hashes=seen,
        ) is True


# --- state: хранение хэшей --------------------------------------------------


def test_state_roundtrip_keeps_post_hashes(tmp_path):
    path = tmp_path / "state.json"
    state = State(recent_post_hashes=["abc123", "def456"])
    save_state(path, state, max_links=10, max_sigs=10)

    loaded = load_state(path)
    assert loaded.recent_post_hashes == ["abc123", "def456"]


def test_old_state_without_hashes_loads_empty(tmp_path):
    """Старый state.json (без поля) грузится, дедуп стартует с нуля."""
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"used_links": ["https://e.com/1"], "used_sigs": []}),
        encoding="utf-8",
    )
    loaded = load_state(path)
    assert loaded.recent_post_hashes == []
    assert loaded.used_links == ["https://e.com/1"]


def test_post_hashes_are_capped(tmp_path):
    path = tmp_path / "state.json"
    state = State(recent_post_hashes=[f"h{i}" for i in range(20)])
    save_state(path, state, max_links=10, max_sigs=5)
    loaded = load_state(path)
    assert len(loaded.recent_post_hashes) == 5
    assert loaded.recent_post_hashes[-1] == "h19"


def test_corrupt_hashes_field_is_ignored(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"recent_post_hashes": "не список"}), encoding="utf-8"
    )
    assert load_state(path).recent_post_hashes == []