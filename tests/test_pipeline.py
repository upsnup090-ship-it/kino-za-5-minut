"""Тесты пайплайна."""

from unittest.mock import MagicMock, patch

from kino.collect import FeedItem
from kino.config import Config, Settings, Source
from kino.pipeline import build_caption, run


def _make_config(tmp_path) -> Config:
    return Config(
        sources=[Source(name="test", url="http://example.com/feed")],
        weights={"facts": 35, "news": 25, "when": 15, "poll": 12, "quote": 8, "trailer": 5},
        bad=[],
        sad=[],
        settings=Settings(),
        llm_base_url="http://llm.test",
        llm_api_key="key",
        draft_model="draft-model",
        judge_model="judge-model",
        bot_token="bot-token",
        channel_chat_id="",
        admin_chat_id="",
        state_path=tmp_path / "state.json",
    )


def test_build_caption_includes_headline_and_question():
    draft_data = {
        "headline": "Шапка",
        "text": "Текст поста",
        "question": "Согласны?",
    }
    caption = build_caption(draft_data, "", Config(settings=Settings()))
    assert "Шапка" in caption
    assert "Текст поста" in caption
    assert " Согласны?" in caption


def test_build_caption_escapes_html():
    draft_data = {
        "headline": "<b>Шапка</b>",
        "text": "a & b",
        "question": "",
    }
    caption = build_caption(draft_data, "", Config(settings=Settings()))
    assert "<b>" not in caption
    assert "&lt;b&amp;" not in caption  # проверяем, что экранировано
    assert "&amp;" in caption


def test_run_dry_run_does_not_save_state(tmp_path):
    cfg = _make_config(tmp_path)
    item = FeedItem(
        source="test",
        title="Some movie news",
        link="http://example.com/1",
        pub_date="",
        ts=0.0,
        snippet="snippet",
    )

    with patch("kino.pipeline.collect") as mock_collect, \
         patch("kino.pipeline.choose") as mock_choose, \
         patch("kino.pipeline.draft") as mock_draft, \
         patch("kino.pipeline.judge") as mock_judge, \
         patch("kino.pipeline.find_image_multi") as mock_image, \
         patch("kino.pipeline.save_state") as mock_save_state, \
         patch("kino.pipeline.StateLock") as mock_lock:

        mock_lock.return_value.acquire.return_value = True
        mock_collect.return_value = MagicMock(items=[item], broken=[], total=1, fresh_count=1)
        mock_choose.return_value = MagicMock(
            item=item,
            format="news",
            quality_score=5,
            sad_topic=False,
        )
        mock_draft.return_value = {
            "headline": "Шапка",
            "text": "Текст",
            "question": "Вопрос?",
        }
        mock_judge.return_value = {
            "headline": "Шапка",
            "text": "Текст",
            "question": "Вопрос?",
        }
        mock_image.return_value = MagicMock(url="http://img.test/x.jpg", title="x", lang="en")

        result = run(cfg, dry_run=True)

    assert result.dry_run is True
    assert result.published is False
    assert "Шапка" in result.caption
    mock_save_state.assert_not_called()


def test_run_publish_saves_state(tmp_path):
    cfg = _make_config(tmp_path)
    cfg.channel_chat_id = "-100123"
    item = FeedItem(
        source="test",
        title="Some movie news",
        link="http://example.com/1",
        pub_date="",
        ts=0.0,
        snippet="snippet",
    )

    with patch("kino.pipeline.collect") as mock_collect, \
         patch("kino.pipeline.choose") as mock_choose, \
         patch("kino.pipeline.draft") as mock_draft, \
         patch("kino.pipeline.judge") as mock_judge, \
         patch("kino.pipeline.find_image_multi") as mock_image, \
         patch("kino.pipeline.publish") as mock_publish, \
         patch("kino.pipeline.save_state") as mock_save_state, \
         patch("kino.pipeline.StateLock") as mock_lock:

        mock_lock.return_value.acquire.return_value = True
        mock_collect.return_value = MagicMock(items=[item], broken=[], total=1, fresh_count=1)
        mock_choose.return_value = MagicMock(
            item=item,
            format="news",
            quality_score=5,
            sad_topic=False,
        )
        mock_draft.return_value = {"headline": "Шапка", "text": "Текст", "question": "Вопрос?"}
        mock_judge.return_value = {"headline": "Шапка", "text": "Текст", "question": "Вопрос?"}
        mock_image.return_value = MagicMock(url="http://img.test/x.jpg", title="x", lang="en")
        mock_publish.return_value = True

        result = run(cfg, dry_run=False)

    assert result.dry_run is False
    assert result.published is True
    mock_save_state.assert_called_once()

def test_pick_variation_weights_and_fallback():
    from types import SimpleNamespace
    from kino.pipeline import pick_variation
    from kino.choose import Choice
    from kino.collect import FeedItem

    item = FeedItem(source="s", title="t", link="https://example.com/a", pub_date="", ts=0, snippet="")
    choice = Choice(item=item, format="news", quality_score=0, sad_topic=False)
    settings = SimpleNamespace(post_variations={"photo": 100, "link": 0, "article": 0})
    cfg = SimpleNamespace(settings=settings)
    kinds = {pick_variation(cfg, {}, "", choice)[0] for _ in range(50)}
    assert kinds == {"photo"}

    settings2 = SimpleNamespace(post_variations={})
    cfg2 = SimpleNamespace(settings=settings2)
    assert pick_variation(cfg2, {}, "", choice) == ("photo", "")

    settings3 = SimpleNamespace(post_variations={"link": 100})
    cfg3 = SimpleNamespace(settings=settings3)
    kind, url = pick_variation(cfg3, {}, "", choice)
    assert (kind, url) == ("link", "https://example.com/a")


def test_run_aborts_gracefully_when_draft_fails(tmp_path):
    """LLM недоступен → не креш под cron/serve, а штатный aborted-результат.

    Боевой кейс 18.10: apimart рвал соединение (RemoteDisconnected), DraftError
    вылетал в __main__ как «необработанная ошибка» и убивал процесс.
    """
    from kino.draft import DraftError

    cfg = _make_config(tmp_path)
    item = FeedItem(
        source="test",
        title="Some movie news",
        link="http://example.com/1",
        pub_date="",
        ts=0.0,
        snippet="snippet",
    )

    with patch("kino.pipeline.collect") as mock_collect, \
         patch("kino.pipeline.choose") as mock_choose, \
         patch("kino.pipeline.draft") as mock_draft, \
         patch("kino.pipeline.save_state"), \
         patch("kino.pipeline.StateLock") as mock_lock:

        mock_lock.return_value.acquire.return_value = True
        mock_collect.return_value = MagicMock(items=[item], broken=[], total=1, fresh_count=1)
        mock_choose.return_value = MagicMock(
            item=item, format="news", quality_score=5, sad_topic=False
        )
        mock_draft.side_effect = DraftError(
            "нет соединения с LLM (https://api.apimart.ai/v1/chat/completions): ConnectionError"
        )

        result = run(cfg, dry_run=True)

    assert result.aborted is True
    assert "нет соединения" in result.abort_reason
    assert result.published is False
