"""Сбор статистики качества контента."""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)


def ensure_stats_dir(stats_dir: str) -> None:
    """Гарантирует существование директории для статистики."""
    Path(stats_dir).mkdir(parents=True, exist_ok=True)


def collect_format_stats(format_name: str, source: str, duration: float, 
                        quality_score: int, sad_topic: bool, 
                        draft_metrics: Dict[str, Any], 
                        judge_feedback: Dict[str, Any]) -> None:
    """Сбор статистики по форматам."""
    stats_file = "logs/format_stats.csv"
    
    # Гарантируем существование директории
    ensure_stats_dir("logs")
    
    # Проверяем, существует ли файл
    file_exists = os.path.exists(stats_file)
    
    # Открываем файл в режиме добавления
    with open(stats_file, 'a', newline='', encoding='utf-8') as csvfile:
        fieldnames = [
            'timestamp', 'format', 'source', 'duration', 'quality_score', 
            'sad_topic', 'draft_length', 'has_quote', 'has_video', 'has_facts',
            'keywords_count', 'headline_length', 'word_count', 'sentence_count'
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        
        # Записываем заголовок, если файл новый
        if not file_exists:
            writer.writeheader()
        
        # Подготавливаем данные для записи
        row = {
            'timestamp': datetime.now().isoformat(),
            'format': format_name,
            'source': source,
            'duration': duration,
            'quality_score': quality_score,
            'sad_topic': sad_topic,
            'draft_length': draft_metrics.get('length', 0),
            'has_quote': draft_metrics.get('has_quote', False),
            'has_video': draft_metrics.get('has_video', False),
            'has_facts': draft_metrics.get('has_facts', False),
            'keywords_count': len(draft_metrics.get('keywords', [])),
            'headline_length': draft_metrics.get('headline_length', 0),
            'word_count': draft_metrics.get('word_count', 0),
            'sentence_count': draft_metrics.get('sentence_count', 0)
        }
        
        writer.writerow(row)
        logger.debug("Собрана статистика для формата %s", format_name)


def collect_judge_performance_stats(judge_time: float, quality_score: int, 
                                  reasoning_length: int, prompt_tokens: int,
                                  completion_tokens: int) -> None:
    """Сбор статистики по производительности судьи."""
    stats_file = "logs/judge_performance.csv"
    
    # Гарантируем существование директории
    ensure_stats_dir("logs")
    
    # Проверяем, существует ли файл
    file_exists = os.path.exists(stats_file)
    
    # Открываем файл в режиме добавления
    with open(stats_file, 'a', newline='', encoding='utf-8') as csvfile:
        fieldnames = [
            'timestamp', 'judge_time', 'quality_score', 'reasoning_length',
            'prompt_tokens', 'completion_tokens'
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        
        # Записываем заголовок, если файл новый
        if not file_exists:
            writer.writeheader()
        
        # Подготавливаем данные для записи
        row = {
            'timestamp': datetime.now().isoformat(),
            'judge_time': judge_time,
            'quality_score': quality_score,
            'reasoning_length': reasoning_length,
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens
        }
        
        writer.writerow(row)
        logger.debug("Собрана статистика производительности судьи")


def get_quality_report() -> Dict[str, Any]:
    """Получение отчета по качеству контента."""
    stats_file = "logs/format_stats.csv"
    
    if not os.path.exists(stats_file):
        return {"error": "Файл статистики не найден"}
    
    # Считываем статистику
    format_stats = {}
    try:
        with open(stats_file, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                format_name = row['format']
                if format_name not in format_stats:
                    format_stats[format_name] = {
                        'count': 0,
                        'avg_quality': 0,
                        'total_duration': 0,
                        'sad_count': 0
                    }
                
                stats = format_stats[format_name]
                stats['count'] += 1
                stats['avg_quality'] += int(row['quality_score'])
                stats['total_duration'] += float(row['duration'])
                if row['sad_topic'] == 'True':
                    stats['sad_count'] += 1
            
            # Вычисляем средние значения
            for format_name, stats in format_stats.items():
                stats['avg_quality'] = round(stats['avg_quality'] / stats['count'], 2)
                stats['avg_duration'] = round(stats['total_duration'] / stats['count'], 2)
                
    except Exception as e:
        return {"error": f"Ошибка чтения статистики: {str(e)}"}
    
    return format_stats