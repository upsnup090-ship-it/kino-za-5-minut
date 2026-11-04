"""Связь пайплайна с резервом и франшизами (шаг 9).

Проверяется то, что нельзя проверить на чистых функциях: пайплайн при
реальной публикации пишет франшизы в state и кладёт готовый пост в резерв,
а в dry-run и при reject не делает ни того, ни другого.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kino.config import Config, Settings
from kino.collect import FeedItem
from kino.pipeline import run
from kino.schedule_db import ScheduleDb
from kino.state import State, load_state, save_state

ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path: Path, **kwargs) -> Config:
    settings = dict(
        schedule_mode="slots",
        franchise_cooldown_hours=36,
        franchises_path=str(ROOT / "config" / "franchises.yaml"),
        reserve_max_items=5,
        reserve_ttl_hours=48,
        deadline_seconds=60,
    )
    settings.update(kwargs)
    return Config(
        sources=[],
        weights={"news": 1},
        bad=[],
        sad=[],
        settings=Settings(**settings),
        llm_base_url="https://llm.test",
        llm_api_key="k",
        draft_model="d",
        judge_model="j",
        bot_token="b",
        channel_chat_id="-100",
        admin_chat_id="",
        state_path=tmp_path / "state.json",
        db_path=tmp_path / "kino.db",
        stats_path=None,
    )


def _item(title: str = "Dune 3 gets release date", link: str = "https://x/dune3") -> FeedItem:
    # Кино-контекст в сниппете: фильтр релевантности choose() поднимает
    # RuntimeError на нуле (см. fix «политэкономия проходит как кино»),
    # тестовые заголовки одни не дают порога.
    return FeedItem(
        source="Deadline",
        title=title,
        link=link,
        pub_date="2026-10-31",
        ts=1793436000.0,
        # Кино-контекст: фильтр релевантности choose() поднимает RuntimeError
        # на score=0 (fix «политэкономия проходит как кино»), тестовые
        # заголовки одни порога не достигают.
        snippet=f"{title}. The movie premiere filming wrapped as the film crew finished production",
        images=["https://img/1.jpg"],
    )


def _run(cfg: Config, *, dry_run: bool, verdict: str = "accept", item: FeedItem | None = None):
    feed_item = item or _item()
    collected = SimpleNamespace(items=[feed_item], broken=[], total=1, fresh_count=1)
    draft_data = {"headline": "Заголовок", "text": "Текст поста"}
    judge_data = {"headline": "Заголовок", "text": "Текст поста", "quality": 8}

    with patch("kino.pipeline.collect", return_value=collected), \
         patch("kino.pipeline.draft", return_value=draft_data), \
         patch("kino.pipeline.judge_with_analysis", return_value=judge_data), \
         patch("kino.variation.find_image_multi",
               return_value=SimpleNamespace(url="https://img/1.jpg", title="", lang="")), \
         patch("kino.variation.find_images", return_value=[]), \
         patch("kino.pipeline.publish", return_value=verdict != "reject"), \
         patch("kino.pipeline.build_caption_text", return_value="Подпись поста"), \
         patch("kino.pipeline.record_format_stat"):
        return run(cfg, dry_run=dry_run)


def test_published_post_goes_to_reserve(tmp_path):
    cfg = _cfg(tmp_path)
    result = _run(cfg, dry_run=False)

    assert result.published is True
    items = ScheduleDb(cfg.db_path).list_reserve()
    assert len(items) == 1
    assert items[0].link == "https://x/dune3"
    assert items[0].caption == "Подпись поста"


def test_published_post_records_franchise(tmp_path):
    cfg = _cfg(tmp_path)
    _run(cfg, dry_run=False)

    state = load_state(cfg.state_path)
    keys = [entry[0] for entry in state.franchise_history]
    assert keys == ["dune"]


def test_dry_run_does_not_touch_reserve_or_franchises(tmp_path):
    cfg = _cfg(tmp_path)
    _run(cfg, dry_run=True)

    assert ScheduleDb(cfg.db_path).list_reserve() == []
    assert load_state(cfg.state_path).franchise_history == []


def test_reject_does_not_touch_reserve_or_franchises(tmp_path):
    cfg = _cfg(tmp_path)
    result = _run(cfg, dry_run=False, verdict="reject")

    assert result.published is False
    assert ScheduleDb(cfg.db_path).list_reserve() == []
    assert load_state(cfg.state_path).franchise_history == []


def test_windows_mode_does_not_fill_reserve(tmp_path):
    """Резерв используется только в режиме слотов — в windows он не нужен."""
    cfg = _cfg(tmp_path, schedule_mode="windows")
    _run(cfg, dry_run=False)

    assert ScheduleDb(cfg.db_path).list_reserve() == []


def test_published_post_without_franchise_records_nothing(tmp_path):
    cfg = _cfg(tmp_path)
    _run(cfg, dry_run=False, item=_item("New indie drama wins festival prize", "https://x/indie"))

    state = load_state(cfg.state_path)
    assert state.franchise_history == []
    # Резерв при этом заполняется: франшиза не обязательна.
    assert len(ScheduleDb(cfg.db_path).list_reserve()) == 1


def test_franchise_history_survives_state_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = State()
    state.franchise_history = [["dune", "2026-10-31T21:20:00+03:00"]]
    save_state(path, state, 10, 10)

    loaded = load_state(path)
    assert loaded.franchise_history == [["dune", "2026-10-31T21:20:00+03:00"]]


def test_old_state_without_franchise_history_loads(tmp_path):
    """Старый state.json без нового поля грузится как пустая история."""
    import json

    path = tmp_path / "state.json"
    path.write_text(json.dumps({"used_links": ["https://a/1"], "used_sigs": []}), encoding="utf-8")

    loaded = load_state(path)
    assert loaded.used_links == ["https://a/1"]
    assert loaded.franchise_history == []
