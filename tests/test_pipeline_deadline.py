"""Регрессии дедлайна в пайплайне: abort, пропуск картинки, совместимость."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from kino.collect import FeedItem
from kino.config import Config, Settings, Source
from kino.deadline import Deadline
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


def _item() -> FeedItem:
    return FeedItem(
        source="test", title="Some movie news", link="http://example.com/1",
        pub_date="", ts=0, snippet="s",
    )


def _expired_deadline(monkeypatch, total: float = 60) -> Deadline:
    """Дедлайн, который уже истёк: total=60, а время пролетело 61с."""
    d = Deadline(total)
    monkeypatch.setattr("kino.deadline.time.monotonic", lambda: d.started + total + 1)
    assert d.expired
    return d


def test_expired_deadline_aborts_before_collect(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    with patch("kino.pipeline.collect") as mc, \
         patch("kino.pipeline.StateLock") as ml:
        ml.return_value.acquire.return_value = True
        result = run(cfg, dry_run=True, deadline=_expired_deadline(monkeypatch))
    assert result.aborted is True
    assert result.published is False
    mc.assert_not_called()


def test_expired_deadline_aborts_with_reason(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    with patch("kino.pipeline.StateLock") as ml:
        ml.return_value.acquire.return_value = True
        result = run(cfg, dry_run=True, deadline=_expired_deadline(monkeypatch))
    assert result.abort_reason  # непустая причина
    assert "бюджет" in result.abort_reason


def test_none_deadline_uses_config(tmp_path):
    """Без deadline run() берёт его из конфига и работает как раньше."""
    cfg = _cfg(tmp_path)
    assert cfg.settings.deadline_seconds > 0
    item = _item()
    with patch("kino.pipeline.collect") as mc, \
         patch("kino.pipeline.choose") as mch, \
         patch("kino.pipeline.draft") as md, \
         patch("kino.pipeline.judge_with_analysis") as mj, \
         patch("kino.pipeline.find_image_multi") as mi, \
         patch("kino.pipeline.StateLock") as ml:
        ml.return_value.acquire.return_value = True
        mc.return_value = SimpleNamespace(items=[item], broken=[])
        mch.return_value = SimpleNamespace(item=item, format="news", quality_score=5, sad_topic=False)
        md.return_value = {"headline": "H", "text": "T"}
        mj.return_value = {"headline": "H", "text": "T", "verdict": "accept"}
        mi.return_value = SimpleNamespace(url="http://img.test/x.jpg", title="x", lang="en")
        result = run(cfg, dry_run=True, deadline=None)
    assert result.aborted is False
    assert result.published is False  # dry_run
    assert result.caption == "H\n\nT"  # подпись собралась


def test_aborted_run_does_not_publish(tmp_path, monkeypatch):
    """aborted=True → publish() не вызывается, счётчик поста не растёт."""
    cfg = _cfg(tmp_path)
    with patch("kino.pipeline.StateLock") as ml, \
         patch("kino.pipeline.publish") as mp:
        ml.return_value.acquire.return_value = True
        result = run(cfg, dry_run=False, deadline=_expired_deadline(monkeypatch))
    assert result.aborted is True
    mp.assert_not_called()


def test_default_deadline_positive_in_settings():
    """Дефолтный дедлайн в Settings — положительный."""
    assert Settings().deadline_seconds > 0


def test_deadline_config_loaded_from_yaml():
    """Секция run.deadline_seconds читается из settings.yaml."""
    from kino.config import load_config
    cfg = load_config(env_file=".env.test")
    assert cfg.settings.deadline_seconds >= 1


def test_image_skipped_when_budget_exhausted_before_image(tmp_path, monkeypatch):
    """Дедлайн истёк — картинка не ищется, прогон aborted."""
    cfg = _cfg(tmp_path)
    item = _item()
    with patch("kino.pipeline.collect") as mc, \
         patch("kino.pipeline.choose") as mch, \
         patch("kino.pipeline.draft") as md, \
         patch("kino.pipeline.judge_with_analysis") as mj, \
         patch("kino.pipeline.find_image_multi") as mi, \
         patch("kino.pipeline.StateLock") as ml:
        ml.return_value.acquire.return_value = True
        mc.return_value = SimpleNamespace(items=[item], broken=[])
        mch.return_value = SimpleNamespace(item=item, format="news", quality_score=0, sad_topic=False)
        md.return_value = {"headline": "H", "text": "T"}
        mj.return_value = {"headline": "H", "text": "T", "verdict": "accept"}
        mi.return_value = SimpleNamespace(url="", title="", lang="none")
        result = run(cfg, dry_run=True, deadline=_expired_deadline(monkeypatch))
    assert result.aborted is True
    mi.assert_not_called()

def test_slow_collect_leaves_full_budget_to_llm(tmp_path):
    """Долгий сбор (ретраи Reddit) не съедает бюджет черновика и судьи.

    Боевой кейс 18.10: Reddit душил по IP-лимиту, ретраи сбора выгрызали
    единый бюджет прогона, и LLM-фаза получала «бюджет исчерпан». Теперь
    сбор живёт в собственном дедлайне, а LLM-фаза получает свежий полный.
    """
    import time

    cfg = _cfg(tmp_path)
    cfg.settings.deadline_seconds = 0.1
    item = FeedItem(
        source="test",
        title="Some movie news",
        link="http://example.com/1",
        pub_date="",
        ts=0.0,
        snippet="snippet",
    )
    captured: dict = {}

    def slow_collect(cfg_arg, *, deadline=None):
        time.sleep(0.3)  # втрое дольше всего deadline_seconds
        return SimpleNamespace(items=[item], broken=[], total=1, fresh_count=1)

    def fake_draft(choice, cfg_arg, *, deadline=None):
        captured["deadline"] = deadline
        return {"headline": "Шапка", "text": "Текст", "question": "Вопрос?"}

    with patch("kino.pipeline.collect", side_effect=slow_collect), \
         patch("kino.pipeline.choose") as mock_choose, \
         patch("kino.pipeline.draft", side_effect=fake_draft), \
         patch("kino.pipeline.judge_with_analysis") as mock_judge, \
         patch("kino.pipeline.find_image_multi"), \
         patch("kino.pipeline.save_state"), \
         patch("kino.pipeline.StateLock") as mock_lock:

        mock_lock.return_value.acquire.return_value = True
        mock_choose.return_value = SimpleNamespace(
            item=item, format="news", quality_score=5, sad_topic=False
        )
        mock_judge.return_value = {"headline": "Шапка", "text": "Текст", "question": "Вопрос?"}

        result = run(cfg, dry_run=True)

    assert result.aborted is False
    assert captured["deadline"] is not None
    assert not captured["deadline"].expired
