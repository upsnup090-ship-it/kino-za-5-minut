"""Судья: отключение reasoning у модели.

Боевой кейс (31.10): deepseek-v4-flash — reasoning-модель. На боевом
judge_system.txt (11.5 КБ) она потратила 5572 токена на размышления при
max_tokens=4096 и вернула пустой content с finish_reason="length". Лестница
бюджетов [4096, 16384] не помогала: модель обрывалась до начала JSON.

Решение: llm.judge.disable_reasoning=true -> в тело запроса уходит
reasoning={"enabled": false}. Судье рассуждения не нужны, он применяет чеклист.
"""

from __future__ import annotations

from types import SimpleNamespace

from kino.choose import Choice
from kino.collect import FeedItem
from kino.config import Settings
from kino.judge import build_judge_prompt


def _choice() -> Choice:
    item = FeedItem(
        source="Variety",
        title="Dune 4 announced",
        link="https://example.com/dune4",
        pub_date="2026-10-30",
        ts=0.0,
        snippet="Warner Bros. confirmed production in spring 2027.",
    )
    return Choice(item=item, format="news", quality_score=7, sad_topic=False)


def _cfg(disable_reasoning: bool):
    return SimpleNamespace(
        settings=Settings(
            judge_max_tokens=4096,
            judge_disable_reasoning=disable_reasoning,
        ),
        judge_model="deepseek-v4-flash",
    )


def test_reasoning_disabled_adds_flag_to_body():
    body = build_judge_prompt(_choice(), {"headline": "Дюна 4"}, _cfg(True))
    assert body["reasoning"] == {"enabled": False}


def test_reasoning_enabled_omits_flag():
    """По умолчанию поле не шлём: не все провайдеры понимают reasoning и
    ответят 400 на неизвестный ключ."""
    body = build_judge_prompt(_choice(), {"headline": "Дюна 4"}, _cfg(False))
    assert "reasoning" not in body


def test_flag_does_not_touch_other_fields():
    body = build_judge_prompt(_choice(), {"headline": "Дюна 4"}, _cfg(True))
    assert body["model"] == "deepseek-v4-flash"
    assert body["max_tokens"] == 4096
    assert body["stream"] is False
    assert body["temperature"] == 0.1
    assert len(body["messages"]) == 2


def test_settings_default_is_off():
    """Флаг выключен по умолчанию — чужие конфиги не ломаются."""
    assert Settings().judge_disable_reasoning is False


# --- дата материала в промпте судьи (шаг 2 раунда 16) ----------------------


def _choice_with_ts(ts: float) -> Choice:
    """Тот же материал, что в боевом прогоне 31.10.2026."""
    item = FeedItem(
        source="Variety",
        title="It: Welcome to Derry Surprise Halloween Episode Drops on HBO Max",
        link="https://example.com/derry",
        pub_date="Sat, 31 Oct 2026 16:50:11 +0000",
        ts=ts,
        snippet="A surprise 30-minute episode.",
    )
    return Choice(item=item, format="facts", quality_score=5, sad_topic=False)


def test_judge_prompt_has_pub_date():
    """Судья видит дату материала — иначе правило «сверь год» невыполнимо.

    Боевой дефект: черновик написал «Материал Variety, 2024» на новости от
    31.10.2026, а судья пропустил — сравнивать было не с чем.
    """
    body = build_judge_prompt(
        _choice_with_ts(1793465411.0), {"fact_source": "Variety, 2024"}, _cfg(False)
    )
    user = body["messages"][1]["content"]
    assert "Дата материала: 31 октября 2026" in user
    assert "{pub_date}" not in user


def test_judge_prompt_no_date_when_unknown():
    body = build_judge_prompt(_choice_with_ts(0.0), {"headline": "x"}, _cfg(False))
    user = body["messages"][1]["content"]
    assert "Дата материала: (неизвестна)" in user
    assert "1970" not in user


def test_judge_prompt_date_matches_draft_prompt():
    """Черновик и судья получают ОДНУ дату — иначе сверка бессмысленна."""
    from kino.draft import build_prompt

    ts = 1793499000.0  # 2026-10-31 23:30 UTC -> 1 ноября по Москве
    cfg = SimpleNamespace(
        settings=Settings(judge_max_tokens=4096, judge_disable_reasoning=False),
        judge_model="deepseek-v4-flash",
        draft_model="m",
        llm_base_url="http://llm.test",
        llm_api_key="k",
    )
    draft_user = build_prompt(_choice_with_ts(ts), cfg)["messages"][1]["content"]
    judge_user = build_judge_prompt(_choice_with_ts(ts), {}, cfg)["messages"][1]["content"]
    assert "Дата материала: 1 ноября 2026" in draft_user
    assert "Дата материала: 1 ноября 2026" in judge_user
