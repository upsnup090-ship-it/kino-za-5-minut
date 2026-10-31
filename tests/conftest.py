"""Фикстуры для тестов."""

import pytest
from unittest.mock import patch

from kino.config import Config, Settings
from kino.collect import FeedItem


@pytest.fixture
def mock_draft_response():
    """Мок ответа черновика."""
    return {
        "headline": "Тестовый заголовок",
        "text": "Тестовый текст новости о кино",
        "quote": {
            "speaker": "Актер",
            "text": "Цитата актера о фильме"
        },
        "facts": ["Факт 1", "Факт 2"],
        "review": {
            "rating": 8,
            "verdict": "Хороший фильм"
        }
    }


@pytest.fixture
def mock_judge_response():
    """Мок ответа судьи."""
    return {
        "quality": 9,
        "sad": False,
        "reasoning": "Контент соответствует стандартам"
    }


@pytest.fixture
def sample_feed_item():
    """Пример элемента фида для тестов."""
    return FeedItem(
        source="test",
        title="Тестовый заголовок новости",
        link="https://test.com/article",
        pub_date="2026-10-31",
        ts=1793436000.0,
        snippet="Краткое описание новости о кино",
        images=["https://test.com/image.jpg"],
        video_url="https://youtube.com/watch?v=test"
    )


@pytest.fixture
def mock_config():
    """Мок конфига для тестов."""
    return Config(
        sources=[],
        weights={"news": 8, "trailer": 5, "quote": 10},
        bad=[],
        sad=[],
        settings=Settings(),
        llm_base_url="",
        llm_api_key="",
        draft_model="",
        judge_model="",
        bot_token="test-token",
        channel_chat_id="-100123",
        admin_chat_id="-100456",
        proxy_url="",
        cache_dir="data/cache",
        horizon_dir="horizon/data",
        horizon_summaries_dir="horizon/data/summaries",
        horizon_run=True,
        horizon_hours=24,
        horizon_timeout=600.0
    )


@pytest.fixture
def mock_llm_calls():
    """Моки вызовов LLM."""
    with patch('kino.draft.draft') as mock_draft, \
         patch('kino.judge.judge') as mock_judge:
        
        # Настройка моков по умолчанию
        mock_draft.return_value = {
            "headline": "Тестовый заголовок",
            "text": "Тестовый текст новости о кино",
            "quote": {
                "speaker": "Актер",
                "text": "Цитата актера о фильме"
            }
        }
        
        mock_judge.return_value = {
            "quality": 9,
            "sad": False
        }
        
        yield {
            'draft': mock_draft,
            'judge': mock_judge
        }