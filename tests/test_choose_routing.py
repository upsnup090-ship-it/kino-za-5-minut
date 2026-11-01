"""Интеграция маршрутизации в `choose` (Фаза 4, docs/source-routing-design.md §8.8–8.9).

Проверяется то, ради чего фаза делалась:

- на элементе с `video_url` не выпадают `facts`/`riddle`/`opinion` (§8.8);
- `routing.enabled: false` даёт прежнее распределение (§8.9);
- априорный kind фида из `cfg.sources` доходит до классификатора;
- sad-фильтр применяется после маршрута;
- лог «маршрут: …» пишется на каждый прогон.

Сеть и LLM не нужны: `choose()` работает на чистом `FeedItem` + `Config`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kino.choose import _feed_kind_for, _pick_format_routed, _routing_enabled, choose
from kino.collect import FeedItem
from kino.config import Config, Settings, Source


def _cfg(tmp_path: Path, **overrides) -> Config:
    base = dict(
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
    )
    base.update(overrides)
    return Config(**base)


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


def _choose_one(cfg: Config, item: FeedItem) -> str:
    choice = choose([item], cfg, used_links=set(), used_sigs=[])
    return choice.format


# --- включение/откат ---------------------------------------------------------


def test_routing_enabled_by_default():
    cfg = _cfg(Path("/tmp"))
    assert _routing_enabled(cfg) is True


def test_routing_disabled_via_flag(tmp_path):
    cfg = _cfg(tmp_path, routing={"enabled": False, "fallback": "news"})
    assert _routing_enabled(cfg) is False


def test_routing_enabled_explicit(tmp_path):
    cfg = _cfg(tmp_path, routing={"enabled": True, "fallback": "news"})
    assert _routing_enabled(cfg) is True


# --- априорный kind фида -----------------------------------------------------


def test_feed_kind_from_sources():
    cfg = _cfg(Path("/tmp"))
    assert _feed_kind_for(_item(source="yt"), cfg) == "video"
    assert _feed_kind_for(_item(source="press"), cfg) == "mixed"


def test_feed_kind_unknown_source_is_mixed():
    cfg = _cfg(Path("/tmp"))
    assert _feed_kind_for(_item(source="horizon"), cfg) == "mixed"


# --- §8.8: на видео не выпадают фактурные форматы ----------------------------


def test_video_item_never_picks_facts_or_riddle(tmp_path):
    """Элемент с video_url → классы video/trailer → facts/riddle/opinion
    не допущены. 200 прогонов — ни одного запрещённого формата."""
    cfg = _cfg(tmp_path)
    item = _item(
        source="yt",
        title="Dune Part Three official trailer",
        video_url="https://youtube.com/watch?v=x",
    )
    forbidden = {"facts", "riddle", "opinion"}
    for _ in range(200):
        fmt = _choose_one(cfg, item)
        assert fmt not in forbidden, f"запрещённый формат {fmt} на видео"


def test_video_item_allows_trailer_and_video(tmp_path):
    cfg = _cfg(tmp_path)
    item = _item(
        source="yt",
        title="Dune Part Three official trailer",
        video_url="https://youtube.com/watch?v=x",
    )
    seen: set[str] = set()
    for _ in range(200):
        seen.add(_choose_one(cfg, item))
    # trailer и video допущены для video-класса с video-сигналом.
    assert "trailer" in seen or "video" in seen


def test_plain_news_item_falls_back_to_news(tmp_path):
    """Чистая новость без даты/спикера/видео → только news и poll допущены,
    и news доминирует по весу. facts не должен выпасть."""
    cfg = _cfg(tmp_path)
    item = _item(title="A new movie is coming", snippet="Nothing special here.")
    for _ in range(100):
        fmt = _choose_one(cfg, item)
        assert fmt != "facts"


# --- sad-фильтр после маршрута -----------------------------------------------


def test_sad_topic_overrides_routed_format(tmp_path):
    """Траурная тема: даже если маршрут допустил poll, sad-фильтр вернёт news."""
    cfg = _cfg(tmp_path, sad=["died"])
    item = _item(
        title="Actor died at 45",
        snippet="The director passed away.",
    )
    # sad-фильтр в _pick_format_routed возвращает news для не-SAD_SAFE.
    fmt, kinds, count = _pick_format_routed(item, cfg, sad_topic=True)
    assert fmt == "news"
    assert kinds  # классы всё равно передаются для CSV


def test_sad_topic_keeps_sad_safe_formats(tmp_path):
    """facts/quote/news — SAD_SAFE, их sad-фильтр не трогает."""
    cfg = _cfg(tmp_path, sad=["died"])
    item = _item(
        title="Actor died at 45, colleagues remember him",
        snippet="He said: I will miss him.",
    )
    # speaker-сигнал есть (прямая речь), quote допущен и SAD_SAFE.
    fmt, kinds, count = _pick_format_routed(item, cfg, sad_topic=True)
    assert fmt in {"news", "facts", "quote"}
    assert count >= 1


# --- §8.9: откат -------------------------------------------------------------


def test_disabled_routing_uses_old_pick(tmp_path):
    """routing.enabled: false → прежний выбор вслепую (веса без классов)."""
    cfg = _cfg(tmp_path, routing={"enabled": False, "fallback": "news"})
    item = _item(
        source="yt",
        title="Dune trailer",
        video_url="https://youtube.com/watch?v=x",
    )
    # Старый _pick_format не смотрит на video_url — с большим весом news
    # он выпадет, что при включённой маршрутизации было бы невозможно.
    seen: set[str] = set()
    for _ in range(300):
        seen.add(_choose_one(cfg, item))
    assert "news" in seen  # news допущен вслепую, вес 21 — крупнейший


# --- лог ---------------------------------------------------------------------


def test_routed_pick_logs_route(tmp_path, caplog):
    import logging

    cfg = _cfg(tmp_path)
    item = _item(
        source="yt",
        title="Dune trailer",
        video_url="https://youtube.com/watch?v=x",
    )
    with caplog.at_level(logging.INFO, logger="kino.choose"):
        _pick_format_routed(item, cfg, sad_topic=False)

    route_lines = [r for r in caplog.records if "маршрут:" in r.getMessage()]
    assert len(route_lines) == 1
    assert "допущено=" in route_lines[0].getMessage()
    assert "выбран=" in route_lines[0].getMessage()


def test_disabled_routing_logs_nothing(tmp_path, caplog):
    import logging

    cfg = _cfg(tmp_path, routing={"enabled": False, "fallback": "news"})
    item = _item()
    with caplog.at_level(logging.INFO, logger="kino.choose"):
        choose([item], cfg, used_links=set(), used_sigs=[])

    assert not any("маршрут:" in r.getMessage() for r in caplog.records)


# --- интеграция через choose() -----------------------------------------------


def test_choose_returns_choice_with_routed_format(tmp_path):
    cfg = _cfg(tmp_path)
    item = _item(source="yt", title="Dune trailer",
                 video_url="https://youtube.com/watch?v=x")
    choice = choose([item], cfg, used_links=set(), used_sigs=[])

    assert choice.item.link == item.link
    assert choice.format  # непустой
    assert choice.format not in {"facts", "riddle", "opinion"}


def test_choose_still_works_with_empty_routing(tmp_path):
    """cfg.routing пуст (прямой Config в тестах) — DEFAULT_ROUTING не нужен,
    choose не должен падать."""
    cfg = _cfg(tmp_path, routing={})
    item = _item()
    fmt = _choose_one(cfg, item)
    assert fmt  # непустой, фолбэк news или допущенный