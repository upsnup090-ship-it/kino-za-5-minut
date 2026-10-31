"""judge_with_analysis: обёртка обязана звать judge() с теми же аргументами.

Регресс 31.10: judge_with_analysis(cfg, draft_data, prompt) вызывала
original_judge(cfg, draft_data, prompt), а у judge() сигнатура
(choice, draft_data, cfg). cfg подставлялся на место choice, prompt (None)
на место cfg — живой прогон падал с
AttributeError: 'NoneType' object has no attribute 'judge_model'.
Тесты этого не ловили: judge() покрыт, обёртка — нет.

Тест фиксирует контракт: choice доходит до судьи, cfg доходит до судьи,
deadline пробрасывается, блок analysis добавляется к результату.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from kino.choose import Choice
from kino.collect import FeedItem
from kino.config import Settings
from kino.deadline import Deadline
from kino.judge import judge_with_analysis


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        settings=Settings(judge_max_tokens=2048),
        llm_base_url="http://llm.test",
        llm_api_key="k",
        judge_model="primary",
        judge_fallback_model="",
    )


def _choice(fmt: str = "news") -> Choice:
    item = FeedItem(source="s", title="t", link="l", pub_date="", ts=0, snippet="sn")
    return Choice(item=item, format=fmt, quality_score=0, sad_topic=False)


def test_judge_with_analysis_passes_choice_and_cfg():
    """choice и cfg доходят до judge() в правильных позициях."""
    cfg = _cfg()
    choice = _choice()
    draft_data = {"headline": "H", "text": "T" * 200}
    seen: dict = {}

    def fake_judge(choice_arg, draft_arg, cfg_arg, *, deadline=None):
        seen["choice"] = choice_arg
        seen["draft"] = draft_arg
        seen["cfg"] = cfg_arg
        seen["deadline"] = deadline
        return {"headline": "H", "text": "T" * 200, "verdict": "accept"}

    with patch("kino.judge.judge", side_effect=fake_judge):
        result = judge_with_analysis(choice, draft_data, cfg)

    assert seen["choice"] is choice, "choice не дошёл до judge()"
    assert seen["cfg"] is cfg, "cfg подменён — тот самый баг с AttributeError"
    assert seen["draft"] is draft_data
    assert result["verdict"] == "accept"


def test_judge_with_analysis_forwards_deadline():
    """deadline пробрасывается: бюджет прогона ограничивает судью."""
    cfg = _cfg()
    choice = _choice()
    deadline = Deadline(60)
    seen: dict = {}

    def fake_judge(choice_arg, draft_arg, cfg_arg, *, deadline=None):
        seen["deadline"] = deadline
        return {"verdict": "accept", "text": "T" * 200}

    with patch("kino.judge.judge", side_effect=fake_judge):
        judge_with_analysis(choice, {"text": "T" * 200}, cfg, deadline=deadline)

    assert seen["deadline"] is deadline, "deadline не проброшен в judge()"


def test_judge_with_analysis_adds_analysis_block():
    """К результату добавляется analysis с метриками черновика."""
    cfg = _cfg()
    choice = _choice()
    draft_data = {
        "headline": "Заголовок",
        "text": "Текст новости о фильме. " * 20,
        "quote": {"speaker": "Актёр", "text": "Цитата"},
    }

    def fake_judge(choice_arg, draft_arg, cfg_arg, *, deadline=None):
        return dict(draft_arg, verdict="accept", quality=8)

    with patch("kino.judge.judge", side_effect=fake_judge):
        result = judge_with_analysis(choice, draft_data, cfg)

    assert "analysis" in result
    analysis = result["analysis"]
    assert "draft_metrics" in analysis
    assert "judge_feedback" in analysis
    assert analysis["judge_feedback"]["quality_score"] == 8
    assert analysis["draft_metrics"]["has_quote"] is True
    assert analysis["draft_metrics"]["length"] == len(draft_data["text"])


def test_judge_with_analysis_does_not_mutate_input_result():
    """result — копия: judge() может вернуть тот же dict, что и draft_data."""
    cfg = _cfg()
    choice = _choice()
    draft_data = {"headline": "H", "text": "T" * 200}

    def fake_judge(choice_arg, draft_arg, cfg_arg, *, deadline=None):
        # Возвращаем тот же объект — как делает настоящий judge() при fallback.
        return dict(draft_arg, verdict="accept")

    with patch("kino.judge.judge", side_effect=fake_judge):
        result = judge_with_analysis(choice, draft_data, cfg)

    assert "analysis" in result
    assert "analysis" not in draft_data, "analysis просочился в исходный черновик"


def test_judge_with_analysis_without_deadline():
    """deadline опционален — вызов без него работает."""
    cfg = _cfg()
    choice = _choice()

    def fake_judge(choice_arg, draft_arg, cfg_arg, *, deadline=None):
        assert deadline is None
        return {"verdict": "accept", "text": "T" * 200}

    with patch("kino.judge.judge", side_effect=fake_judge):
        result = judge_with_analysis(choice, {"text": "T" * 200}, cfg)

    assert result["verdict"] == "accept"