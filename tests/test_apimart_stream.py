"""APIMart отдаёт SSE по умолчанию даже без stream:true.

Без явного stream:false ответ приходит как text/event-stream и response.json()
падает с JSONDecodeError. Тесты держат этот контракт.
"""

from __future__ import annotations

from kino.choose import Choice
from kino.collect import FeedItem
from kino.config import Config, Settings
from kino.draft import build_prompt
from kino.judge import build_judge_prompt


def _cfg() -> Config:
    return Config(
        settings=Settings(),
        llm_base_url="https://api.apimart.ai/v1",
        llm_api_key="sk-test",
        draft_model="glm-5.3-flash",
        judge_model="deepseek-v4-flash",
    )


def _choice() -> Choice:
    item = FeedItem(
        source="Variety",
        title="Dune Part Three announced",
        link="http://example.com/1",
        pub_date="",
        ts=0.0,
        snippet="Villeneuve returns to direct.",
    )
    return Choice(item=item, format="news", quality_score=5, sad_topic=False)


def test_draft_body_disables_streaming():
    body = build_prompt(_choice(), _cfg())
    assert body["stream"] is False


def test_judge_body_disables_streaming():
    draft_data = {"headline": "Шапка", "text": "Текст"}
    body = build_judge_prompt(_choice(), draft_data, _cfg())
    assert body["stream"] is False


def test_bodies_use_apimart_models():
    cfg = _cfg()
    assert build_prompt(_choice(), cfg)["model"] == "glm-5.3-flash"
    assert build_judge_prompt(_choice(), {"headline": "x"}, cfg)["model"] == "deepseek-v4-flash"
