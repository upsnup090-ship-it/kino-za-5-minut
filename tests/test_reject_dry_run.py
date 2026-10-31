"""Регрессии reject: dry-run не должен менять state.

Найдено при доработке после боевого прогона: `_reject_run` сохранял state
безусловно, то есть в dry-run (документированная гарантия «state не меняется»)
отклонённая новость всё равно помечалась использованной.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from kino.choose import Choice
from kino.collect import FeedItem
from kino.config import Config, Settings, Source
from kino.pipeline import run


def _cfg(tmp_path) -> Config:
    return Config(
        sources=[Source(name="test", url="http://example.com/feed")],
        weights={"facts": 1},
        bad=[],
        sad=[],
        settings=Settings(),
        llm_base_url="http://llm.test",
        llm_api_key="k",
        draft_model="d",
        judge_model="j",
        bot_token="b",
        channel_chat_id="-100",
        admin_chat_id="",
        state_path=tmp_path / "state.json",
    )


def _run_rejected(cfg: Config, *, dry_run: bool):
    item = FeedItem(
        source="test", title="Some movie news", link="http://example.com/1",
        pub_date="", ts=0, snippet="s",
    )
    with patch("kino.pipeline.collect") as mock_collect, \
         patch("kino.pipeline.choose") as mock_choose, \
         patch("kino.pipeline.draft") as mock_draft, \
         patch("kino.pipeline.judge_with_analysis") as mock_judge, \
         patch("kino.pipeline.save_state") as mock_save, \
         patch("kino.pipeline.StateLock") as mock_lock, \
         patch("kino.publish.notify_admin"):
        mock_lock.return_value.acquire.return_value = True
        mock_collect.return_value = SimpleNamespace(items=[item], broken=[], total=1, fresh_count=1)
        mock_choose.return_value = Choice(item=item, format="news", quality_score=5, sad_topic=False)
        mock_draft.return_value = {"headline": "H", "text": "T"}
        mock_judge.return_value = {"headline": "H", "text": "T", "verdict": "reject", "reason": "нет", "analysis": {"judge_feedback": {}}}
        result = run(cfg, dry_run=dry_run)
    return result, mock_save


def test_reject_in_dry_run_does_not_save_state(tmp_path):
    result, mock_save = _run_rejected(_cfg(tmp_path), dry_run=True)
    assert result.verdict == "reject"
    assert result.published is False
    mock_save.assert_not_called()


def test_reject_in_publish_mode_saves_state(tmp_path):
    """В боевом режиме ссылку помечаем, иначе цикл на той же новости."""
    result, mock_save = _run_rejected(_cfg(tmp_path), dry_run=False)
    assert result.verdict == "reject"
    mock_save.assert_called_once()


def test_reject_returns_empty_caption_and_no_image(tmp_path):
    result, _ = _run_rejected(_cfg(tmp_path), dry_run=False)
    assert result.caption == ""
    assert result.image_url == ""