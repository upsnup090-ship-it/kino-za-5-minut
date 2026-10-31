"""Тесты резерва (шаг 9): хранение, лимиты, чистка, связь с конфигом."""

from __future__ import annotations

from pathlib import Path

import pytest

from kino.config import Config, Settings
from kino.reserve import purge, stats, store
from kino.schedule_db import ScheduleDb


def _cfg(tmp_path: Path, **kwargs) -> Config:
    settings = dict(
        reserve_max_items=5,
        reserve_ttl_hours=48,
        schedule_mode="slots",
    )
    settings.update(kwargs)
    return Config(
        sources=[], weights={"news": 1}, bad=[], sad=[],
        settings=Settings(**settings),
        state_path=tmp_path / "state.json",
        db_path=tmp_path / "kino.db",
    )


def _store(cfg: Config, link: str = "https://a/1", **kwargs) -> bool:
    params = dict(
        link=link,
        title="Заголовок",
        source="Deadline",
        format="news",
        draft_data={"headline": "Заголовок", "text": "текст"},
        caption="Подпись",
        image_url="https://img/1.jpg",
        kind="photo",
    )
    params.update(kwargs)
    return store(cfg, **params)  # type: ignore[arg-type]


def test_store_saves_post(tmp_path):
    cfg = _cfg(tmp_path)
    assert _store(cfg) is True

    items = ScheduleDb(cfg.db_path).list_reserve()
    assert len(items) == 1
    item = items[0]
    assert item.link == "https://a/1"
    assert item.title == "Заголовок"
    assert item.format == "news"
    assert item.caption == "Подпись"
    assert item.kind == "photo"
    assert '"headline"' in item.draft_json


def test_store_rejects_empty_link(tmp_path):
    cfg = _cfg(tmp_path)
    assert _store(cfg, link="") is False


def test_store_rejects_duplicate(tmp_path):
    cfg = _cfg(tmp_path)
    assert _store(cfg) is True
    assert _store(cfg) is False
    assert len(ScheduleDb(cfg.db_path).list_reserve()) == 1


def test_store_respects_max_items(tmp_path):
    cfg = _cfg(tmp_path, reserve_max_items=2)
    assert _store(cfg, link="https://a/1") is True
    assert _store(cfg, link="https://a/2") is True
    assert _store(cfg, link="https://a/3") is False
    assert len(ScheduleDb(cfg.db_path).list_reserve()) == 2


def test_store_uses_ttl_from_config(tmp_path):
    cfg = _cfg(tmp_path, reserve_ttl_hours=1)
    _store(cfg)
    item = ScheduleDb(cfg.db_path).list_reserve()[0]
    assert not item.is_expired


def test_store_survives_broken_db_path(tmp_path):
    """Недоступная БД не роняет прогон: пост уже вышел."""
    blocker = tmp_path / "blocker"
    blocker.write_text("не каталог", encoding="utf-8")
    cfg = _cfg(tmp_path)
    cfg.db_path = blocker / "kino.db"
    assert _store(cfg) is False


def test_stats_counts_available(tmp_path):
    cfg = _cfg(tmp_path)
    assert stats(cfg) == (0, 0)
    _store(cfg, link="https://a/1")
    _store(cfg, link="https://a/2")
    assert stats(cfg) == (2, 2)

    db = ScheduleDb(cfg.db_path)
    db.take_reserve()
    assert stats(cfg) == (1, 2)


def test_purge_removes_used(tmp_path):
    cfg = _cfg(tmp_path)
    _store(cfg, link="https://a/1")
    _store(cfg, link="https://a/2")
    ScheduleDb(cfg.db_path).take_reserve()

    assert purge(cfg) == 1
    assert stats(cfg) == (1, 1)


def test_purge_on_empty_db(tmp_path):
    cfg = _cfg(tmp_path)
    assert purge(cfg) == 0


def test_stats_survives_broken_db(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    cfg = _cfg(tmp_path)
    cfg.db_path = blocker / "kino.db"
    assert stats(cfg) == (0, 0)
    assert purge(cfg) == 0


@pytest.mark.parametrize("kind", ["photo", "link", "text", "poll", "gallery", "article"])
def test_store_keeps_kind(tmp_path, kind):
    cfg = _cfg(tmp_path)
    assert _store(cfg, kind=kind) is True
    assert ScheduleDb(cfg.db_path).list_reserve()[0].kind == kind
