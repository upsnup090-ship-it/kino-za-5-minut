"""Регрессии выбора вариации и отказа судьи.

Пункты 6, 7 из docs/todo.md.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from kino.choose import Choice
from kino.collect import FeedItem
from kino.config import Config, Settings, Source
from kino.judge import judge
from kino.pipeline import _VARIATIONS, pick_variation, run


def _choice(link: str = "https://example.com/a") -> Choice:
    item = FeedItem(source="s", title="t", link=link, pub_date="", ts=0, snippet="")
    return Choice(item=item, format="news", quality_score=0, sad_topic=False)


def _cfg(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(settings=SimpleNamespace(**kwargs))


# --- п.6: мусор в весах не роняет прогон ------------------------------------


def test_pick_variation_survives_non_numeric_weight():
    cfg = _cfg(post_variations={"photo": "abc"})
    kind, _ = pick_variation(cfg, {}, "", _choice())
    assert kind == "photo"


def test_pick_variation_survives_none_weight():
    cfg = _cfg(post_variations={"photo": None})
    kind, _ = pick_variation(cfg, {}, "", _choice())
    assert kind == "photo"


def test_pick_variation_ignores_negative_weight():
    cfg = _cfg(post_variations={"photo": -5, "text": 1})
    for _ in range(20):
        kind, _ = pick_variation(cfg, {}, "", _choice())
        assert kind == "text"


def test_pick_variation_ignores_unknown_keys():
    cfg = _cfg(post_variations={"unknown": 10, "photo": 1})
    kind, _ = pick_variation(cfg, {}, "", _choice())
    assert kind == "photo"


def test_pick_variation_survives_non_dict_config():
    cfg = _cfg(post_variations="не словарь")
    kind, _ = pick_variation(cfg, {}, "", _choice())
    assert kind == "photo"


def test_pick_variation_defaults_to_photo_when_all_zero():
    cfg = _cfg(post_variations={v: 0 for v in _VARIATIONS})
    assert pick_variation(cfg, {}, "", _choice()) == ("photo", "")


def test_pick_variation_link_without_url_falls_back():
    cfg = _cfg(post_variations={"link": 100})
    kind, url = pick_variation(cfg, {}, "", _choice(link=""))
    assert (kind, url) == ("photo", "")


def test_pick_variation_link_returns_news_url():
    cfg = _cfg(post_variations={"link": 100})
    kind, url = pick_variation(cfg, {}, "", _choice())
    assert (kind, url) == ("link", "https://example.com/a")


# --- п.7: reject не публикуется ---------------------------------------------


def _judge_response(payload, status: int = 200, *, finish_reason: str = "stop", reasoning: str = ""):
    """Ответ судьи. payload — dict (сериализуем) или готовая строка content."""
    content = payload if isinstance(payload, str) else json.dumps(payload)

    class _Response:
        status_code = status
        text = "err"

        def json(self):
            return {
                "choices": [
                    {
                        "finish_reason": finish_reason,
                        "message": {"content": content, "reasoning_content": reasoning},
                    }
                ]
            }

    return _Response()


def _judge_cfg(**kwargs) -> Config:
    return Config(
        settings=Settings(),
        llm_base_url="http://llm.test",
        llm_api_key="k",
        judge_model="primary",
        **kwargs,
    )


def test_judge_returns_reject_verdict_and_reason():
    cfg = _judge_cfg()
    payload = {"verdict": "reject", "reason": "факт не подтверждён", "headline": "H"}
    with patch("kino.judge._post_chat", return_value=_judge_response(payload)):
        out = judge(_choice(), {"headline": "H", "text": "T"}, cfg)
    assert out["verdict"] == "reject"
    assert out["reason"] == "факт не подтверждён"


def test_judge_keeps_draft_hashtags_when_judge_omits_them():
    cfg = _judge_cfg()
    payload = {"verdict": "accept", "headline": "H2", "text": "T2"}
    draft = {"headline": "H", "text": "T", "hashtags": ["кино"], "key_moment": "🔥 K"}
    with patch("kino.judge._post_chat", return_value=_judge_response(payload)):
        out = judge(_choice(), draft, cfg)
    assert out["hashtags"] == ["кино"]
    assert out["key_moment"] == "🔥 K"


def test_judge_retries_primary_with_bigger_budget_before_fallback():
    """Сначала основная модель с удвоенным бюджетом, только потом фолбэк."""
    cfg = _judge_cfg(judge_fallback_model="backup")
    calls: list[tuple[str, int]] = []

    def fake_post(url, json=None, **kwargs):
        calls.append((json["model"], json["max_tokens"]))
        if json["model"] == "primary":
            return _judge_response({}, status=500)
        return _judge_response({"verdict": "accept", "headline": "H2"})

    with patch("kino.judge._post_chat", side_effect=fake_post), \
         patch("kino.judge._notify_fallback"):
        out = judge(_choice(), {"headline": "H", "text": "T"}, cfg)

    assert [model for model, _ in calls] == ["primary", "primary", "backup"]
    # Бюджет второй попытки больше первой — это и есть лестница.
    assert calls[1][1] > calls[0][1]
    assert out["verdict"] == "accept"


def test_judge_falls_back_to_second_model():
    cfg = _judge_cfg(judge_fallback_model="backup")
    calls: list[str] = []

    def fake_post(url, json=None, **kwargs):
        calls.append(json["model"])
        if json["model"] == "primary":
            return _judge_response({}, status=500)
        return _judge_response({"verdict": "accept", "headline": "H2"})

    with patch("kino.judge._post_chat", side_effect=fake_post), \
         patch("kino.judge._notify_fallback"):
        out = judge(_choice(), {"headline": "H", "text": "T"}, cfg)
    assert calls[0] == "primary"
    assert calls[-1] == "backup"
    assert out["verdict"] == "accept"


def test_judge_empty_content_with_length_retries_with_bigger_budget():
    """Reasoning съел бюджет: content пустой — повторяем, а не сдаёмся."""
    cfg = _judge_cfg()
    budgets: list[int] = []

    def fake_post(url, json=None, **kwargs):
        budgets.append(json["max_tokens"])
        if len(budgets) == 1:
            return _judge_response("", finish_reason="length", reasoning="р" * 3000)
        return _judge_response({"verdict": "accept", "headline": "H2"})

    with patch("kino.judge._post_chat", side_effect=fake_post), \
         patch("kino.judge._notify_fallback"):
        out = judge(_choice(), {"headline": "H", "text": "T"}, cfg)

    assert len(budgets) == 2
    assert budgets[1] > budgets[0]
    assert out["verdict"] == "accept"


def test_judge_accepts_truncated_but_parsable_response():
    """Обрезанный, но разбираемый ответ не считаем провалом."""
    cfg = _judge_cfg()
    calls: list[int] = []

    def fake_post(url, json=None, **kwargs):
        calls.append(json["max_tokens"])
        return _judge_response('{"verdict":"accept","headline":"H2"', finish_reason="length")

    with patch("kino.judge._post_chat", side_effect=fake_post), \
         patch("kino.judge._notify_fallback"):
        out = judge(_choice(), {"headline": "H", "text": "T"}, cfg)

    assert len(calls) == 1
    assert out["verdict"] == "accept"


def test_judge_without_fallback_marks_fallback():
    cfg = _judge_cfg()
    with patch("kino.judge._post_chat", return_value=_judge_response({}, status=500)), \
         patch("kino.judge._notify_fallback"):
        out = judge(_choice(), {"headline": "H", "text": "T"}, cfg)
    assert out["verdict"] == "fallback"


def _pipeline_cfg(tmp_path) -> Config:
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


def test_run_does_not_publish_rejected_draft(tmp_path):
    cfg = _pipeline_cfg(tmp_path)
    item = FeedItem(
        source="test", title="Some movie news", link="http://example.com/1",
        pub_date="", ts=0, snippet="s",
    )
    with patch("kino.pipeline.collect") as mock_collect, \
         patch("kino.pipeline.choose") as mock_choose, \
         patch("kino.pipeline.draft") as mock_draft, \
         patch("kino.pipeline.judge") as mock_judge, \
         patch("kino.pipeline.publish") as mock_publish, \
         patch("kino.pipeline.StateLock") as mock_lock, \
         patch("kino.pipeline.save_state") as mock_save, \
         patch("kino.publish.notify_admin"):
        mock_lock.return_value.acquire.return_value = True
        mock_collect.return_value = SimpleNamespace(items=[item], broken=[], total=1, fresh_count=1)
        mock_choose.return_value = Choice(item=item, format="news", quality_score=5, sad_topic=False)
        mock_draft.return_value = {"headline": "H", "text": "T"}
        mock_judge.return_value = {"headline": "H", "text": "T", "verdict": "reject", "reason": "нет"}
        result = run(cfg, dry_run=False)

    assert result.published is False
    assert result.verdict == "reject"
    assert result.caption == ""
    mock_publish.assert_not_called()
    # Ссылку всё равно помечаем использованной, иначе следующий прогон
    # возьмёт ту же новость и снова упрётся в reject.
    mock_save.assert_called_once()

def test_judge_network_error_goes_through_ladder_and_fallback():
    """Сетевой провал _post_chat не убивает судью: лестница + фолбэк-модель."""
    from kino.draft import DraftError

    cfg = _judge_cfg(judge_fallback_model="backup")
    calls: list[str] = []

    def fake_post(cfg_arg, body, **kwargs):
        calls.append(body["model"])
        if body["model"] == "primary":
            raise DraftError("нет соединения с LLM (http://llm.test): ConnectionError")
        return _judge_response({"verdict": "accept", "headline": "H2"})

    with patch("kino.judge._post_chat", side_effect=fake_post), \
         patch("kino.judge._notify_fallback"):
        out = judge(_choice(), {"headline": "H", "text": "T"}, cfg)

    # primary: базовый бюджет → удвоенный → фолбэк backup
    assert calls == ["primary", "primary", "backup"]
    assert out["verdict"] == "accept"
