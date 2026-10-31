"""Тесты анализатора качества."""

import pytest

from kino.analyzer import (
    analyze_draft_quality, 
    analyze_judge_feedback, 
    evaluate_content_quality,
    generate_recommendations
)


def test_analyze_draft_quality():
    """Тест анализа качества черновика."""
    draft_data = {
        "headline": "Тестовый заголовок",
        "text": "Это тестовый текст с несколькими предложениями. Он достаточно длинный для проверки.",
        "quote": {"speaker": "Актер", "text": "Цитата актера"},
        "video_url": "https://youtube.com/watch?v=test",
        "facts": ["Факт 1", "Факт 2"],
        "review": {"rating": 8, "verdict": "Хороший фильм"}
    }
    
    result = analyze_draft_quality(draft_data)
    
    assert isinstance(result, dict)
    assert result["length"] == len(draft_data["text"])
    assert result["has_quote"] == True
    assert result["has_video"] == True
    assert result["has_facts"] == True
    assert result["has_review"] == True
    assert "keywords" in result
    assert result["headline_length"] == len(draft_data["headline"])
    assert result["word_count"] > 0
    assert result["sentence_count"] > 0


def test_analyze_judge_feedback():
    """Тест анализа обратной связи судьи."""
    judge_data = {
        "quality": 9,
        "sad": False,
        "reasoning": "Контент соответствует стандартам"
    }
    
    result = analyze_judge_feedback(judge_data)
    
    assert isinstance(result, dict)
    assert result["quality_score"] == 9
    assert result["is_sad"] == False
    assert result["has_reasoning"] == True
    assert result["reasoning_length"] == len(judge_data["reasoning"])


def test_evaluate_content_quality():
    """Тест оценки качества контента."""
    draft_data = {
        "headline": "Тест",
        "text": "Короткий текст",
        "quote": {"speaker": "Актер", "text": "Цитата"}
    }
    
    judge_data = {
        "quality": 8,
        "sad": False,
        "reasoning": "Хороший контент"
    }
    
    result = evaluate_content_quality(draft_data, judge_data)
    
    assert isinstance(result, dict)
    assert "draft_metrics" in result
    assert "judge_feedback" in result
    assert "overall_quality" in result
    assert "recommendations" in result


def test_generate_recommendations():
    """Тест генерации рекомендаций."""
    draft_metrics = {
        "length": 50,
        "has_quote": False,
        "has_video": False,
        "has_facts": False,
        "headline_length": 10
    }
    
    judge_feedback = {
        "quality_score": 4,
        "is_sad": False,
        "has_reasoning": True,
        "reasoning_length": 20
    }
    
    recommendations = generate_recommendations(draft_metrics, judge_feedback)
    
    assert isinstance(recommendations, list)
    assert len(recommendations) > 0


def test_empty_data():
    """Тест обработки пустых данных."""
    empty_draft = {}
    empty_judge = {}
    
    draft_result = analyze_draft_quality(empty_draft)
    judge_result = analyze_judge_feedback(empty_judge)
    eval_result = evaluate_content_quality(empty_draft, empty_judge)
    
    assert isinstance(draft_result, dict)
    assert isinstance(judge_result, dict)
    assert isinstance(eval_result, dict)