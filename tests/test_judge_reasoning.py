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
