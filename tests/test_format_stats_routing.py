"""Статистика маршрутизации в format_stats.csv (Фаза 5, §7).

Проверяется:

- `record_format_stat` пишет новые колонки `kinds` и `allowed_count`;
- `Choice` несёт классы и число допущенных из `choose()`;
- старый CSV (4 колонки) не пересоздаётся — история не бьётся;
- `kinds` сериализуется через `|`, пустой — пустая строка.

Сеть и LLM не нужны: `record_format_stat` — чистая запись в файл.
"""

from __future__ import annotations

import csv
from pathlib import Path

from kino.choose import Choice, choose
from kino.collect import FeedItem
from kino.config import Config, Settings, Source
from kino.pipeline import record_format_stat


def _cfg(tmp_path: Path, stats: bool = True) -> Config:
    return Config(
        sources=[
            Source(name="press", url="https://press.example/rss", kind="mixed"),
            Source(name="yt", url="https://yt.example/rss", kind="video"),
        ],
        weights={"news": 21, "facts": 8, "when": 16, "trailer": 5, "video": 5,
                 "poll": 7, "quote": 6, "riddle": 4, "opinion": 5},
        bad=[], sad=[],
        settings=Settings(),
        admin_chat_id="-100500",
        state_path=tmp_path / "state.json",
        db_path=tmp_path / "kino.db",
        stats_path=(tmp_path / "format_stats.csv") if stats else None,
    )


def _item(**overrides) -> FeedItem:
    base = dict(
        source="press",
        title="Dune Part Three gets a release date",
        link="https://press.example/a",
        pub_date="2026-10-31",
        ts=1793436000.0,
        snippet="The studio announced the premiere window.",
        images=[],
        video_url="",
    )
    base.update(overrides)
    return FeedItem(**base)


def _choice(**overrides) -> Choice:
    base = dict(
        item=_item(),
        format="news",
        quality_score=5,
        sad_topic=False,
        kinds=("news", "release_date"),
        allowed_count=4,
    )
    base.update(overrides)
    return Choice(**base)


# --- record_format_stat ------------------------------------------------------


def test_new_file_has_six_columns(tmp_path):
    cfg = _cfg(tmp_path)
    record_format_stat(cfg, _choice())

    with cfg.stats_path.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        row = next(reader)

    assert header == ["timestamp", "format", "source", "quality_score", "kinds", "allowed_count"]
    assert len(row) == 6


def test_kinds_joined_with_pipe(tmp_path):
    cfg = _cfg(tmp_path)
    record_format_stat(cfg, _choice(kinds=("news", "release_date")))

    with cfg.stats_path.open(encoding="utf-8") as f:
        rows = list(csv.reader(f))

    assert rows[1][4] == "news|release_date"


def test_allowed_count_written(tmp_path):
    cfg = _cfg(tmp_path)
    record_format_stat(cfg, _choice(allowed_count=4))

    with cfg.stats_path.open(encoding="utf-8") as f:
        rows = list(csv.reader(f))

    assert rows[1][5] == "4"


def test_empty_kinds_is_empty_string(tmp_path):
    """Маршрутизация выключена → kinds пуст, в CSV пустая строка, не мусор."""
    cfg = _cfg(tmp_path)
    record_format_stat(cfg, _choice(kinds=(), allowed_count=0))

    with cfg.stats_path.open(encoding="utf-8") as f:
        rows = list(csv.reader(f))

    assert rows[1][4] == ""
    assert rows[1][5] == "0"


def test_existing_four_column_file_migrated(tmp_path):
    """Старый CSV (4 колонки) мигрируется: заголовок и строки до 6 колонок.

    Иначе новая строка из 6 полей в файле с 4-колоночным заголовком била бы
    CSV — строки разной ширины. Миграция сохраняет историю и делает файл
    валидным.
    """
    cfg = _cfg(tmp_path)
    cfg.stats_path.write_text(
        "timestamp,format,source,quality_score\n"
        "2026-10-31T00:00:00+00:00,news,press,5\n",
        encoding="utf-8",
    )
    record_format_stat(cfg, _choice())

    with cfg.stats_path.open(encoding="utf-8") as f:
        rows = list(csv.reader(f))

    # Заголовок дополнен, старая строка дополнена пустыми полями.
    assert rows[0] == ["timestamp", "format", "source", "quality_score", "kinds", "allowed_count"]
    assert rows[1] == ["2026-10-31T00:00:00+00:00", "news", "press", "5", "", ""]
    # Новая строка — полные 6 полей.
    assert len(rows[2]) == 6
    assert rows[2][4] == "news|release_date"
    assert rows[2][5] == "4"


def test_already_six_column_file_untouched(tmp_path):
    """6-колоночный файл не мигрируется повторно."""
    cfg = _cfg(tmp_path)
    cfg.stats_path.write_text(
        "timestamp,format,source,quality_score,kinds,allowed_count\n"
        "2026-10-31T00:00:00+00:00,news,press,5,news,2\n",
        encoding="utf-8",
    )
    record_format_stat(cfg, _choice())

    with cfg.stats_path.open(encoding="utf-8") as f:
        rows = list(csv.reader(f))

    assert len(rows) == 3  # заголовок + старая + новая
    assert rows[0] == ["timestamp", "format", "source", "quality_score", "kinds", "allowed_count"]


def test_foreign_file_not_touched(tmp_path):
    """Чужой CSV (не наш заголовок) не мигрируется."""
    cfg = _cfg(tmp_path)
    cfg.stats_path.write_text("foo,bar\n1,2\n", encoding="utf-8")
    record_format_stat(cfg, _choice())

    with cfg.stats_path.open(encoding="utf-8") as f:
        rows = list(csv.reader(f))

    assert rows[0] == ["foo", "bar"]  # заголовок не тронут


def test_no_stats_path_is_noop(tmp_path):
    cfg = _cfg(tmp_path, stats=False)
    record_format_stat(cfg, _choice())  # не должно падать

    assert not (tmp_path / "format_stats.csv").exists()


# --- Choice несёт классы из choose() -----------------------------------------


def test_choose_populates_kinds_and_count(tmp_path):
    cfg = _cfg(tmp_path)
    item = _item(source="yt", title="Dune trailer",
                 video_url="https://youtube.com/watch?v=x")
    choice = choose([item], cfg, used_links=set(), used_sigs=[])

    assert choice.kinds  # непустой: video/trailer классы
    assert "video" in choice.kinds or "trailer" in choice.kinds
    assert choice.allowed_count >= 1


def test_choose_disabled_routing_has_empty_kinds(tmp_path):
    cfg = _cfg(tmp_path, stats=False)
    cfg.routing = {"enabled": False, "fallback": "news"}
    item = _item()
    choice = choose([item], cfg, used_links=set(), used_sigs=[])

    assert choice.kinds == ()
    assert choice.allowed_count == 0