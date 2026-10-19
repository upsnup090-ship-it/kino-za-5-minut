"""Судья: обрезанный ответ — повтор с большим бюджетом, а не salvage как успех.

Боевой кейс (живая сессия 1, 19.10): судья упиралась в llm.judge.max_tokens=2048,
спасала часть полей и считалась «справившейся». Лестница бюджетов [2048, 16384]
из-за этого не запускалась, и правки судьи (включая drop_fields) брались из
обрезанного JSON.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from kino.choose import Choice
from kino.collect import FeedItem
from kino.config import Settings
from kino.constants import BUDGET_CEILING
from kino.judge import judge


def _cfg(fallback_model: str = ""):
    return SimpleNamespace(
        settings=Settings(judge_max_tokens=2048),
        llm_base_url="http://llm.test",
        llm_api_key="k",
        judge_model="primary",
        judge_fallback_model=fallback_model,
    )


def _choice(fmt: str = "news") -> Choice:
    item = FeedItem(source="s", title="t", link="l", pub_date="", ts=0, snippet="sn")
    return Choice(item=item, format=fmt, quality_score=0, sad_topic=False)


def _response(content: str, finish_reason: str = "stop"):
    class _Response:
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [
                    {"finish_reason": finish_reason, "message": {"content": content}}
                ]
            }

    return _Response()


def test_truncated_judge_retries_with_bigger_budget():
    """Обрезка → повтор со ступенью выше; финальное решение — из целого ответа."""
    budgets: list[int] = []

    def fake_post(cfg, body, *, deadline=None):
        budgets.append(body["max_tokens"])
        if len(budgets) == 1:
            return _response('{"verdict":"accept","headline":"Частичный"', "length")
        return _response('{"verdict":"accept","headline":"Целый","text":"T"}')

    with patch("kino.judge._post_chat", side_effect=fake_post):
        out = judge(_choice(), {"headline": "старый", "text": "старый"}, _cfg())

    assert out["headline"] == "Целый"
    assert budgets == [2048, BUDGET_CEILING]


def test_clean_judge_response_calls_once():
    """Целый ответ не тратит вторую ступень бюджета."""
    budgets: list[int] = []

    def fake_post(cfg, body, *, deadline=None):
        budgets.append(body["max_tokens"])
        return _response('{"verdict":"accept","headline":"Целый"}')

    with patch("kino.judge._post_chat", side_effect=fake_post):
        out = judge(_choice(), {"headline": "старый", "text": "T"}, _cfg())

    assert out["headline"] == "Целый"
    assert budgets == [2048]


def test_all_judge_budgets_truncated_uses_salvaged():
    """Все ступени обрезаны — берём спасённое, но решение неполное (не падаем)."""

    def fake_post(cfg, body, *, deadline=None):
        return _response('{"verdict":"accept","headline":"Спасённый"', "length")

    with patch("kino.judge._post_chat", side_effect=fake_post):
        out = judge(_choice(), {"headline": "старый", "text": "T"}, _cfg())

    assert out["headline"] == "Спасённый"
    assert out["verdict"] == "accept"


def test_truncated_primary_prefers_clean_fallback_model():
    """Если все ступени основной модели обрезаны — целый ответ фолбэка лучше."""
    seen: list[tuple[str, int]] = []

    def fake_post(cfg, body, *, deadline=None):
        seen.append((body["model"], body["max_tokens"]))
        if body["model"] == "fallback":
            return _response('{"verdict":"accept","headline":"От фолбэка"}')
        return _response('{"verdict":"accept","headline":"Частичный"', "length")

    with patch("kino.judge._post_chat", side_effect=fake_post):
        out = judge(_choice(), {"headline": "старый", "text": "T"}, _cfg("fallback"))

    assert out["headline"] == "От фолбэка"
    assert seen == [
        ("primary", 2048),
        ("primary", BUDGET_CEILING),
        ("fallback", 2048),
    ]


def test_judge_budgets_ladder_shape():
    from kino.judge import _judge_budgets

    assert _judge_budgets(2048) == [2048, BUDGET_CEILING]
    # Некорректно малый лимит заменяется дефолтом, а не даёт max_tokens=0.
    assert _judge_budgets(0) == [1500, BUDGET_CEILING]