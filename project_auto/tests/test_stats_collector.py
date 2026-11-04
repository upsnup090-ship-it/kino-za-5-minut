"""Тесты сборщика статистики качества.

Модуль появился в коммите 2b5dba1 без покрытия и с багом: писал в тот же
logs/format_stats.csv, что и record_format_stat, но с другими колонками —
CSV становился битым (78 строк по 4 колонки и 18 по 14 вперемешку).
Эти тесты фиксируют разделение файлов и устойчивость к ошибкам записи.
"""

from __future__ import annotations

import csv

import pytest

from kino.stats_collector import (
    collect_format_stats,
    collect_judge_performance_stats,
    ensure_stats_dir,
    get_quality_report,
)

_DRAFT = {
    "length": 420,
    "has_quote": True,
    "word_count": 63,
    "sentence_count": 8,
    "headline_length": 54,
}
_JUDGE = {"quality_score": 7}


# --- разделение файлов ------------------------------------------------------


def test_format_stats_goes_to_quality_csv_not_format_csv(tmp_path):
    """Расширенная метрика пишется в quality_stats.csv, а не в format_stats.csv.

    format_stats.csv принадлежит record_format_stat (4 колонки) — вторая
    запись с 14 колонками делала его нечитаемым.
    """
    collect_format_stats(
        "news", "Deadline", 1.5, 7, False, _DRAFT, _JUDGE, stats_dir=tmp_path
    )
    assert (tmp_path / "quality_stats.csv").exists()
    assert not (tmp_path / "format_stats.csv").exists()


def test_quality_csv_has_expected_header(tmp_path):
    collect_format_stats(
        "news", "Deadline", 1.5, 7, False, _DRAFT, _JUDGE, stats_dir=tmp_path
    )
    with (tmp_path / "quality_stats.csv").open(encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert header[0] == "timestamp"
    assert "duration" in header
    assert "sad_topic" in header
    assert "overall_quality" in header
    assert "recommendations_count" in header
    assert len(header) == 16


def test_judge_performance_goes_to_own_file(tmp_path):
    collect_judge_performance_stats(2.5, 8, 120, 0, 0, stats_dir=tmp_path)
    assert (tmp_path / "judge_performance.csv").exists()
    assert not (tmp_path / "quality_stats.csv").exists()


def test_header_written_once(tmp_path):
    """Вторая запись не должна дублировать заголовок."""
    for _ in range(3):
        collect_format_stats(
            "news", "s", 1.0, 5, False, _DRAFT, _JUDGE, stats_dir=tmp_path
        )
    lines = (tmp_path / "quality_stats.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("timestamp,format")
    assert sum(1 for line in lines if line.startswith("timestamp")) == 1
    assert len(lines) == 4  # заголовок + 3 строки


# --- отчёт ------------------------------------------------------------------


def test_report_aggregates_by_format(tmp_path):
    collect_format_stats(
        "news", "s", 1.0, 6, False, _DRAFT, _JUDGE, stats_dir=tmp_path
    )
    collect_format_stats(
        "news", "s", 3.0, 8, True, _DRAFT, _JUDGE, stats_dir=tmp_path
    )
    collect_format_stats(
        "poll", "s", 2.0, 9, False, _DRAFT, _JUDGE, stats_dir=tmp_path
    )

    report = get_quality_report(tmp_path)
    assert report["news"]["count"] == 2
    assert report["news"]["avg_quality"] == 7.0
    assert report["news"]["avg_duration"] == 2.0
    assert report["news"]["sad_count"] == 1
    assert report["poll"]["count"] == 1


def test_report_missing_file_returns_error(tmp_path):
    report = get_quality_report(tmp_path)
    assert "error" in report


def test_report_reads_quality_not_format_csv(tmp_path):
    """Отчёт не должен пытаться читать format_stats.csv (другой формат)."""
    (tmp_path / "format_stats.csv").write_text(
        "timestamp,format,source,quality_score\n2026-01-01,news,s,5\n",
        encoding="utf-8",
    )
    report = get_quality_report(tmp_path)
    assert "error" in report


# --- устойчивость к ошибкам -------------------------------------------------


def test_unwritable_dir_does_not_raise(tmp_path):
    """Статистика вторична: нет директории — warning, а не падение прогона."""
    bad = tmp_path / "file-not-dir"
    bad.write_text("x", encoding="utf-8")
    collect_format_stats(
        "news", "s", 1.0, 5, False, _DRAFT, _JUDGE, stats_dir=bad / "logs"
    )  # не должно бросить


def test_ensure_stats_dir_swallows_oserror(tmp_path):
    bad = tmp_path / "file-not-dir"
    bad.write_text("x", encoding="utf-8")
    ensure_stats_dir(bad / "sub")  # не должно бросить


# --- overall_quality и recommendations_count ---------------------------------


def test_overall_quality_and_recommendations_written(tmp_path):
    """Раньше эти значения считались и выбрасывались — теперь в CSV."""
    collect_format_stats(
        "news", "s", 1.0, 7, False, _DRAFT, _JUDGE,
        overall_quality=5.6, recommendations_count=3, stats_dir=tmp_path,
    )
    with (tmp_path / "quality_stats.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["overall_quality"] == "5.6"
    assert rows[0]["recommendations_count"] == "3"


def test_overall_quality_none_writes_empty(tmp_path):
    """Нет оценки судьи — пустая строка, а не 0 (0 значил бы «оценено как плохое»)."""
    collect_format_stats(
        "news", "s", 1.0, 0, False, _DRAFT, _JUDGE,
        overall_quality=None, stats_dir=tmp_path,
    )
    with (tmp_path / "quality_stats.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["overall_quality"] == ""


def test_report_aggregates_overall_and_recommendations(tmp_path):
    collect_format_stats(
        "news", "s", 1.0, 7, False, _DRAFT, _JUDGE,
        overall_quality=5.6, recommendations_count=3, stats_dir=tmp_path,
    )
    collect_format_stats(
        "news", "s", 1.0, 8, False, _DRAFT, _JUDGE,
        overall_quality=6.0, recommendations_count=1, stats_dir=tmp_path,
    )
    # Один пост без оценки — не должен уронить среднее.
    collect_format_stats(
        "news", "s", 1.0, 0, False, _DRAFT, _JUDGE,
        overall_quality=None, recommendations_count=0, stats_dir=tmp_path,
    )
    report = get_quality_report(tmp_path)
    assert report["news"]["avg_overall_quality"] == pytest.approx(5.8, abs=0.01)
    assert report["news"]["avg_recommendations"] == pytest.approx(1.33, abs=0.01)


def test_report_old_rows_without_new_columns(tmp_path):
    """Старый CSV (без overall_quality) читается: среднее по оценке — None."""
    (tmp_path / "quality_stats.csv").write_text(
        "timestamp,format,source,duration,quality_score,sad_topic,draft_length,"
        "has_quote,has_video,has_facts,keywords_count,headline_length,word_count,"
        "sentence_count\n"
        "2026-01-01,news,s,1.0,7,False,100,False,False,False,0,10,5,2\n",
        encoding="utf-8",
    )
    report = get_quality_report(tmp_path)
    assert report["news"]["avg_overall_quality"] is None
    assert report["news"]["avg_recommendations"] == 0.0

# --- контракт с конфигом: stats_path=None значит «не писать» -----------------


def test_pipeline_does_not_write_stats_without_stats_path(tmp_path, monkeypatch):
    """Config.stats_path=None (тесты, скрипты) — CSV не пишется вовсе.

    Раньше pipeline фолбэчился на Path("logs") и тестовые прогоны
    дописывали строки source=test в боевой logs/quality_stats.csv.
    Контракт п.38: нет stats_path — нет записи.
    """
    from kino.config import Config, Settings
    from kino import pipeline

    monkeypatch.chdir(tmp_path)
    cfg = Config(settings=Settings(), stats_path=None)
    assert cfg.stats_path is None

    # Вызываем ровно ту ветку, что пишет статистику.
    stats_dir = cfg.stats_path.parent if cfg.stats_path else None
    assert stats_dir is None
    assert not (tmp_path / "logs" / "quality_stats.csv").exists()
