"""Тесты анализатора качества.

Раньше эти тесты подавали на вход {"quality": 9, "sad": False, "reasoning": ...}
— объект, которого judge() никогда не возвращал (полей не было в схеме судьи).
Тест был зелёным, а в бою quality_score всегда выходил 0. Теперь поля есть в
схеме judge_system.txt и пробрасываются через judge(), поэтому здесь
проверяется реальный контракт, включая отсутствие оценки.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from kino.analyzer import (
    STOP_WORDS,
    WEAK_WORDS,
    analyze_draft_quality,
    analyze_judge_feedback,
    evaluate_content_quality,
    extract_keywords,
    generate_recommendations,
)
from kino.choose import Choice
from kino.collect import FeedItem
from kino.config import Config, Settings
from kino.judge import judge

# --- метрики черновика ------------------------------------------------------


def test_analyze_draft_quality():
    draft_data = {
        "headline": "Тестовый заголовок",
        "text": "Это тестовый текст с несколькими предложениями. Он достаточно длинный.",
        "quote": {"speaker": "Актер", "text": "Цитата актера"},
        "video_url": "https://youtube.com/watch?v=test",
        "facts": ["Факт 1", "Факт 2"],
        "review": {"rating": 8, "verdict": "Хороший фильм"},
    }
    result = analyze_draft_quality(draft_data)

    assert result["length"] == len(draft_data["text"])
    assert result["has_quote"] is True
    assert result["has_video"] is True
    assert result["has_facts"] is True
    assert result["has_review"] is True
    assert result["headline_length"] == len(draft_data["headline"])
    assert result["word_count"] > 0
    assert result["sentence_count"] > 0


def test_draft_quality_detects_links_numbers_quotes():
    draft = {"text": 'Текст https://e.com «цитата» и 2027 год "в кавычках"'}
    result = analyze_draft_quality(draft)
    assert result["has_links"] is True
    assert result["has_numbers"] is True
    assert result["quote_count"] == 1


def test_empty_data_does_not_crash():
    draft_result = analyze_draft_quality({})
    judge_result = analyze_judge_feedback({})
    eval_result = evaluate_content_quality({}, {})
    assert draft_result["length"] == 0
    assert judge_result["quality_score"] is None
    assert eval_result["overall_quality"] is None


# --- ключевые слова ---------------------------------------------------------


def test_keywords_drop_service_words():
    text = "Вышел новый трейлер фильма Дюна. Режиссёр Дени Вильнёв подтвердил премьеру."
    words = extract_keywords(text)
    assert "дюна" in words
    assert "вильнёв" in words
    for stop in ("это", "было", "если", "который"):
        assert stop not in words


def test_keywords_drop_weak_generic_words():
    """«вышел», «новый», «фильм» есть в каждом посте — как ключевые бесполезны."""
    text = "Вышел новый фильм. Фильм получил награду."
    words = extract_keywords(text)
    assert "вышел" not in words
    assert "новый" not in words
    assert "фильм" not in words


def test_keywords_keep_short_proper_nouns():
    """«Дюна», «Нолан», «Оскар» — значимые, несмотря на длину."""
    words = extract_keywords("Фильм Дюна. Нолан взял Оскар.")
    assert "дюна" in words
    assert "нолан" in words
    assert "оскар" in words


def test_keywords_order_is_deterministic():
    """Раньше стоял list(set(...)) — порядок зависел от хеш-сида процесса."""
    text = "Дюна Вильнёв Шаламе Зендея премьера трейлер студия кадры сезон Netflix"
    orders = {tuple(extract_keywords(text)) for _ in range(20)}
    assert len(orders) == 1


def test_keywords_deduplicated_in_order_of_appearance():
    words = extract_keywords("Дюна и снова Дюна, Дюна везде")
    assert words.count("дюна") == 1
    assert words[0] == "дюна"


def test_keywords_limit():
    text = " ".join(f"слово{i}" for i in range(30))
    assert len(extract_keywords(text)) == 10


def test_keywords_empty_and_stopwords_only():
    assert extract_keywords("") == []
    assert extract_keywords("это было и есть при том, который весь") == []


def test_stop_and_weak_sets_do_not_overlap():
    """Один и тот же список дважды — источник расхождений при правках."""
    assert not (STOP_WORDS & WEAK_WORDS)


@pytest.mark.parametrize("title", ["Дюна", "Нолан", "Оскар", "Канны", "Netflix"])
def test_titles_are_not_filtered(title):
    assert title.lower() not in STOP_WORDS
    assert title.lower() not in WEAK_WORDS


# --- обратная связь судьи ---------------------------------------------------


def test_analyze_judge_feedback_with_quality():
    judge_data = {"quality": 9, "sad": False, "reasoning": "Конкретика есть."}
    result = analyze_judge_feedback(judge_data)
    assert result["quality_score"] == 9
    assert result["is_sad"] is False
    assert result["has_reasoning"] is True
    assert result["reasoning_length"] == len(judge_data["reasoning"])


def test_missing_quality_is_none_not_zero():
    """Нет оценки — None. Ноль означал бы «оценено как плохое»."""
    result = analyze_judge_feedback({"verdict": "accept"})
    assert result["quality_score"] is None
    assert result["is_sad"] is False
    assert result["has_reasoning"] is False


def test_sad_flag_is_read():
    assert analyze_judge_feedback({"quality": 6, "sad": True})["is_sad"] is True


# --- сводка и рекомендации --------------------------------------------------


def test_overall_quality_uses_real_score():
    draft = {"text": "Текст " * 50}
    result = evaluate_content_quality(draft, {"quality": 8})
    # 8 * 0.7 + min(300/1000, 1) * 0.3 = 5.6 + 0.09
    assert result["overall_quality"] == pytest.approx(5.69, abs=0.01)


def test_overall_quality_none_without_score():
    result = evaluate_content_quality({"text": "Текст"}, {"verdict": "accept"})
    assert result["overall_quality"] is None


def test_low_quality_recommendation_only_on_real_low_score():
    """Раньше score был всегда 0, и «Контент низкого качества» шло на каждый пост."""
    good = generate_recommendations({"length": 500}, {"quality_score": 9})
    assert "Контент низкого качества, перепроверьте информацию" not in good

    bad = generate_recommendations({"length": 500}, {"quality_score": 3})
    assert "Контент низкого качества, перепроверьте информацию" in bad


def test_no_quality_recommendation_when_score_unknown():
    recs = generate_recommendations({"length": 500}, {"quality_score": None})
    assert "Контент низкого качества, перепроверьте информацию" not in recs


def test_generate_recommendations_on_weak_draft():
    draft_metrics = {
        "length": 50,
        "has_quote": False,
        "has_video": False,
        "has_facts": False,
        "headline_length": 10,
    }
    recs = generate_recommendations(draft_metrics, {"quality_score": 4})
    assert isinstance(recs, list)
    assert any("короткий" in r for r in recs)
    assert any("цитату" in r for r in recs)


# --- сквозной контракт с judge() --------------------------------------------


def _cfg() -> Config:
    return Config(
        settings=Settings(),
        judge_model="m",
        llm_base_url="http://x",
        llm_api_key="k",
    )


def _choice() -> Choice:
    item = FeedItem(
        source="Deadline", title="Dune", link="https://e.com/1",
        pub_date="", ts=0.0, snippet="d",
    )
    return Choice(item=item, format="news", quality_score=3, sad_topic=False)


def _judge_with_reply(reply: dict) -> dict:
    draft = {"headline": "H", "text": "Текст поста достаточно длинный."}
    with patch("kino.judge._request_judge", lambda *a, **k: (dict(reply), "", False)):
        return judge(_choice(), draft, _cfg())


def test_judge_propagates_quality_fields():
    """Схема судьи включает quality/sad/reasoning — они должны дойти до анализа."""
    out = _judge_with_reply({
        "verdict": "accept", "quality": 8, "sad": False,
        "reasoning": "Названия и дата есть, язык живой.",
    })
    assert out["quality"] == 8
    assert out["sad"] is False
    assert out["reasoning"].startswith("Названия")

    feedback = analyze_judge_feedback(out)
    assert feedback["quality_score"] == 8
    assert feedback["has_reasoning"] is True


def test_judge_reject_still_carries_quality():
    """reject — про факты, quality — про текст. Одно не отменяет другое."""
    out = _judge_with_reply({
        "verdict": "reject", "reason": "выдуман факт",
        "quality": 8, "sad": False, "reasoning": "Написано хорошо.",
    })
    assert out["verdict"] == "reject"
    assert out["quality"] == 8


@pytest.mark.parametrize("bad", [0, 11, 99, -1, "8", None, 7.5])
def test_judge_ignores_invalid_quality(bad):
    out = _judge_with_reply({"verdict": "accept", "quality": bad})
    assert "quality" not in out


def test_judge_ignores_non_bool_sad():
    out = _judge_with_reply({"verdict": "accept", "sad": "yes"})
    assert "sad" not in out


def test_judge_ignores_blank_reasoning():
    out = _judge_with_reply({"verdict": "accept", "reasoning": "   "})
    assert "reasoning" not in out