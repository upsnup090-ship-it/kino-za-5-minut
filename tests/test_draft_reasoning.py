"""Регрессии черновика: reasoning-модель съедает бюджет и не отдаёт JSON.

Найдено живым прогоном: glm-5.3-flash при finish_reason=length возвращал
content="" (весь бюджет ушёл в reasoning_content), а pipeline падал
необработанным DraftError — под cron это тихая смерть прогона.
"""

from __future__ import annotations

import pytest

from kino.choose import Choice
from kino.collect import FeedItem
from kino.config import Config, Settings
from kino.draft import DraftError, draft


def _choice() -> Choice:
    item = FeedItem(source="s", title="t", link="l", pub_date="", ts=0, snippet="sn")
    return Choice(item=item, format="news", quality_score=0, sad_topic=False)


def _cfg() -> Config:
    return Config(
        settings=Settings(draft_max_tokens=1500),
        llm_base_url="http://llm.test",
        llm_api_key="k",
        draft_model="glm-5.3-flash",
    )


def _response(content: str, finish_reason: str = "stop", reasoning: str = ""):
    class _Response:
        status_code = 200

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


def test_empty_content_with_length_raises_clear_error(monkeypatch):
    """Пустой content при length — это не «нет JSON», а съеденный бюджет.

    Модель отдаёт пустой content на всех попытках лестницы, поэтому наружу
    уходит DraftError с указанием последнего бюджета и причины.
    """
    monkeypatch.setattr(
        "kino.draft.requests.post",
        lambda *a, **kw: _response("", "length", "р" * 3000),
    )
    with pytest.raises(DraftError, match="reasoning"):
        draft(_choice(), _cfg())


def test_truncated_draft_retries_with_bigger_budget(monkeypatch):
    """Reasoning съел бюджет — повторяем с удвоенным max_tokens, а не сдаёмся."""
    budgets: list[int] = []

    def fake_post(url, json=None, **kw):
        budgets.append(json["max_tokens"])
        # Первый бюджет (1500) съеден reasoning, на удвоенном (3000) выходит.
        if json["max_tokens"] < 3000:
            return _response("", "length", "р" * 8000)
        return _response('{"headline":"A","text":"B"}')

    monkeypatch.setattr("kino.draft.requests.post", fake_post)
    result = draft(_choice(), _cfg())
    assert result["headline"] == "A"
    assert budgets == [1500, 3000]


def test_budget_ladder_grows_and_dedupes():
    from kino.draft import _BUDGET_CEILING, _budget_ladder

    # Верхняя ступень не ниже потолка: reasoning занимает до ~10.5k символов.
    assert _budget_ladder(4096) == [4096, 8192, _BUDGET_CEILING]
    assert _budget_ladder(1500) == [1500, 3000, _BUDGET_CEILING]
    # Некорректный базовый лимит не должен давать max_tokens=0.
    assert _budget_ladder(0) == [1500, 3000, _BUDGET_CEILING]
    assert _budget_ladder(-5) == [1500, 3000, _BUDGET_CEILING]
    assert _budget_ladder(1) == [1500, 3000, _BUDGET_CEILING]
    # По возрастанию, без дублей, не больше трёх попыток.
    ladder = _budget_ladder(4096)
    assert ladder == sorted(set(ladder))
    assert len(ladder) == 3


def test_budget_ceiling_covers_measured_reasoning():
    """Замер на живом API: reasoning до ~10.5k символов (~3.2k токенов)."""
    from kino.draft import _BUDGET_CEILING

    assert _BUDGET_CEILING >= 8192


def test_truncated_but_started_json_is_salvaged_without_retry(monkeypatch):
    """JSON начался — спасаем поля, повтор не нужен."""
    calls: list[int] = []

    def fake_post(url, json=None, **kw):
        calls.append(json["max_tokens"])
        return _response('{"headline":"A","text":"B"', "length")

    monkeypatch.setattr("kino.draft.requests.post", fake_post)
    result = draft(_choice(), _cfg())
    assert result["headline"] == "A"
    assert len(calls) == 1


def test_normal_response_parses(monkeypatch):
    monkeypatch.setattr(
        "kino.draft.requests.post",
        lambda *a, **kw: _response('{"headline":"A","text":"B","hashtags":["кино"]}'),
    )
    result = draft(_choice(), _cfg())
    assert result["hashtags"] == ["кино"]


def test_empty_content_without_length_still_reports_no_json(monkeypatch):
    """Пустой content при finish_reason=stop — обычный случай «нет JSON»."""
    monkeypatch.setattr("kino.draft.requests.post", lambda *a, **kw: _response("", "stop"))
    with pytest.raises(DraftError, match="пустой ответ"):
        draft(_choice(), _cfg())

# --- сетевые сбои провайдера (боевой кейс apimart 18.10) ---------------------


def test_post_chat_retries_on_connection_error():
    """RemoteDisconnected от провайдера — транзиентно: ретрай, потом успех."""
    from unittest.mock import patch

    import requests

    from kino.draft import _post_chat

    cfg = _cfg()
    ok = _response("{}", "stop")
    calls: list[str] = []

    def fake_post(url, **kwargs):
        calls.append(url)
        if len(calls) < 3:
            raise requests.exceptions.ConnectionError("Remote end closed")
        return ok

    with patch("kino.draft.requests.post", side_effect=fake_post), \
         patch("kino.draft.time.sleep"):
        resp = _post_chat(cfg, {"model": "m"})

    assert resp is ok
    assert len(calls) == 3


def test_post_chat_gives_up_after_retries():
    from unittest.mock import patch

    import requests

    from kino.draft import DraftError, _post_chat

    cfg = _cfg()
    with patch("kino.draft.requests.post",
               side_effect=requests.exceptions.ConnectionError("boom")), \
         patch("kino.draft.time.sleep"):
        with pytest.raises(DraftError, match="нет соединения"):
            _post_chat(cfg, {"model": "m"})


def test_request_draft_returns_error_on_network_failure():
    """Сетевая ошибка идёт через лестницу (None, error), а не крешом."""
    from unittest.mock import patch

    from kino.draft import DraftError, _request_draft

    cfg = _cfg()
    with patch("kino.draft._post_chat", side_effect=DraftError("нет соединения")):
        parsed, content, error = _request_draft(cfg, {"model": "m"})
    assert parsed is None
    assert "нет соединения" in error
