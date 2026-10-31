"""Тесты пайплайна."""

import pytest
from unittest.mock import patch

from kino.config import Config, Settings
from kino.collect import FeedItem
from kino.pipeline import run


def test_pipeline_runs_with_mocked_components():
    """Тест запуска пайплайна с mocked компонентами."""
    
    # Подготавливаем конфиг
    config = Config(
        sources=[],
        weights={"news": 8, "trailer": 5},
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
    
    # Подготавливаем тестовый элемент
    item = FeedItem(
        source="test",
        title="Тестовый заголовок",
        link="https://test.com/article",
        pub_date="2026-10-31",
        ts=1793436000.0,
        snippet="Краткое описание",
        images=["https://test.com/image.jpg"],
        video_url="https://youtube.com/watch?v=test"
    )
    
    # Мокаем все компоненты
    with patch('kino.pipeline.collect') as mock_collect, \
         patch('kino.draft.draft') as mock_draft, \
         patch('kino.judge.judge') as mock_judge, \
         patch('kino.pipeline.save_state') as mock_save_state:
        
        # Настройка моков
        mock_collect.return_value = type('CollectResult', (), {
            'items': [item],
            'broken': [],
            'total': 1,
            'fresh_count': 1
        })()
        
        mock_draft.return_value = {
            "headline": "Тестовый заголовок",
            "text": "Тестовый текст"
        }
        mock_judge.return_value = {"quality": 9, "sad": False}
        
        # Выполнение
        result = run(config, dry_run=True)
        
        # Проверка
        assert result is not None
        assert result.format is not None
        assert result.dry_run == True
        assert result.published == False


def test_pipeline_with_different_weights():
    """Тест пайплайна с разными весами форматов."""
    
    config = Config(
        sources=[],
        weights={"news": 100, "facts": 1, "quote": 1},  # Сильный bias к news
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
    
    item = FeedItem(
        source="test",
        title="Тестовый заголовок",
        link="https://test.com/article",
        pub_date="2026-10-31",
        ts=1793436000.0,
        snippet="Краткое описание",
        images=[],
        video_url=""
    )
    
    with patch('kino.pipeline.collect') as mock_collect, \
         patch('kino.draft.draft') as mock_draft, \
         patch('kino.judge.judge') as mock_judge:
        
        mock_collect.return_value = type('CollectResult', (), {
            'items': [item],
            'broken': [],
            'total': 1,
            'fresh_count': 1
        })()
        
        mock_draft.return_value = {
            "headline": "Тестовый заголовок",
            "text": "Тестовый текст"
        }
        mock_judge.return_value = {"quality": 9, "sad": False}
        
        # Выполнение
        result = run(config, dry_run=True)
        
        # Проверка
        assert result is not None
        assert result.format is not None