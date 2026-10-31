"""Сбор статистики качества контента.

Отдельный файл от `format_stats.csv` (его пишет `record_format_stat` в
pipeline.py): тот CSV — 4 колонки для routing_report.py, этот — расширенная
метрика качества (14 колонок). Раньше оба писали в один файл с разными
заголовками, и CSV становился битым (строки разной ширины).

Пути берутся из параметров, а не хардкодятся: вызов из pipeline передаёт
cfg.stats_path.parent, чтобы уважать STATS_PATH из окружения.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


def ensure_stats_dir(stats_dir: str | Path) -> None:
    """Гарантирует существование директории для статистики.

    Метрика вторична по отношению к посту: если директорию создать нельзя
    (нет прав, странный путь), это не повод ронять прогон — пишем warning
    и идём дальше (запись ниже упадёт на OSError и тоже будет проглочена).
    """
    try:
        Path(stats_dir).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("не удалось создать директорию статистики %s: %s", stats_dir, exc)


def collect_format_stats(
    format_name: str,
    source: str,
    duration: float,
    quality_score: int,
    sad_topic: bool,
    draft_metrics: Dict[str, Any],
    judge_feedback: Dict[str, Any],
    *,
    stats_dir: str | Path = "logs",
) -> None:
    """Дописывает строку расширенной метрики качества в quality_stats.csv.

    Не трогает format_stats.csv — тот пишет record_format_stat. Ошибки
    записи не роняют пайплайн (метрика вторична по отношению к посту).
    """
    stats_file = Path(stats_dir) / "quality_stats.csv"
    ensure_stats_dir(stats_file.parent)

    fieldnames = [
        "timestamp", "format", "source", "duration", "quality_score",
        "sad_topic", "draft_length", "has_quote", "has_video", "has_facts",
        "keywords_count", "headline_length", "word_count", "sentence_count",
    ]
    row = {
        "timestamp": datetime.now().isoformat(),
        "format": format_name,
        "source": source,
        "duration": duration,
        "quality_score": quality_score,
        "sad_topic": sad_topic,
        "draft_length": draft_metrics.get("length", 0),
        "has_quote": draft_metrics.get("has_quote", False),
        "has_video": draft_metrics.get("has_video", False),
        "has_facts": draft_metrics.get("has_facts", False),
        "keywords_count": len(draft_metrics.get("keywords", [])),
        "headline_length": draft_metrics.get("headline_length", 0),
        "word_count": draft_metrics.get("word_count", 0),
        "sentence_count": draft_metrics.get("sentence_count", 0),
    }
    try:
        new_file = not stats_file.exists()
        with stats_file.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if new_file:
                writer.writeheader()
            writer.writerow(row)
    except OSError as exc:
        logger.warning("не удалось записать статистику качества: %s", exc)
        return
    logger.debug("собрана статистика качества для формата %s", format_name)


def collect_judge_performance_stats(
    judge_time: float,
    quality_score: int,
    reasoning_length: int,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    stats_dir: str | Path = "logs",
) -> None:
    """Дописывает строку производительности судьи в judge_performance.csv."""
    stats_file = Path(stats_dir) / "judge_performance.csv"
    ensure_stats_dir(stats_file.parent)

    fieldnames = [
        "timestamp", "judge_time", "quality_score", "reasoning_length",
        "prompt_tokens", "completion_tokens",
    ]
    row = {
        "timestamp": datetime.now().isoformat(),
        "judge_time": judge_time,
        "quality_score": quality_score,
        "reasoning_length": reasoning_length,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    try:
        new_file = not stats_file.exists()
        with stats_file.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if new_file:
                writer.writeheader()
            writer.writerow(row)
    except OSError as exc:
        logger.warning("не удалось записать статистику судьи: %s", exc)
        return
    logger.debug("собрана статистика производительности судьи")


def get_quality_report(stats_dir: str | Path = "logs") -> Dict[str, Any]:
    """Сводка по качеству из quality_stats.csv (не format_stats.csv)."""
    stats_file = Path(stats_dir) / "quality_stats.csv"

    if not stats_file.exists():
        return {"error": "Файл статистики качества не найден"}

    format_stats: Dict[str, Dict[str, Any]] = {}
    try:
        with stats_file.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                format_name = row["format"]
                if format_name not in format_stats:
                    format_stats[format_name] = {
                        "count": 0,
                        "avg_quality": 0.0,
                        "total_duration": 0.0,
                        "sad_count": 0,
                    }
                stats = format_stats[format_name]
                stats["count"] += 1
                stats["avg_quality"] += int(row["quality_score"])
                stats["total_duration"] += float(row["duration"])
                if row["sad_topic"] == "True":
                    stats["sad_count"] += 1

            for stats in format_stats.values():
                stats["avg_quality"] = round(
                    stats["avg_quality"] / stats["count"], 2
                )
                stats["avg_duration"] = round(
                    stats["total_duration"] / stats["count"], 2
                )
    except (OSError, ValueError, KeyError) as exc:
        return {"error": f"Ошибка чтения статистики: {exc}"}

    return format_stats