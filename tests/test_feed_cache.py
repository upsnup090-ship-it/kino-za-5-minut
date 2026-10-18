"""Регрессии кэша фидов.

Найдено боевым прогоном: окно лимита Reddit держится около минуты, и пять
фидов в один прогон не влезают. Без кэша 4 источника из 5 терялись на каждом
запуске; с кэшем при 429 берётся последний удачный снимок.
"""

from __future__ import annotations

import pytest
import requests

from kino.collect import (
    _fetch_cached,
    _fetch_one,
    _read_cache,
    _write_cache,
    collect,
)
from kino.config import Config, Settings, Source

_FEED = (
    b'<?xml version="1.0"?><rss version="2.0"><channel>'
    b"<item><title>Dune 3</title><link>https://e.com/1</link>"
    b"<description>desc</description></item></channel></rss>"
)


def _cfg(tmp_path, **kwargs) -> Config:
    return Config(
        sources=[],
        settings=Settings(**kwargs),
        llm_base_url="http://llm.test",
        llm_api_key="k",
        bot_token="b",
        cache_dir=tmp_path / "cache",
    )


def _source(name: str = "Reddit/movies", url: str = "https://reddit.com/r/movies/.rss") -> Source:
    return Source(name=name, url=url)


# --- запись и чтение кэша ---------------------------------------------------


def test_cache_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    _write_cache(cfg, "Reddit/movies", _FEED)
    assert _read_cache(cfg, "Reddit/movies") == _FEED


def test_cache_missing_returns_none(tmp_path):
    assert _read_cache(_cfg(tmp_path), "нет такого") is None


def test_cache_name_is_slugged(tmp_path):
    """Имя источника с «/» не должно создавать вложенный путь."""
    cfg = _cfg(tmp_path)
    _write_cache(cfg, "Reddit/movies", _FEED)
    files = list(cfg.cache_dir.glob("*.xml"))
    assert len(files) == 1
    assert "/" not in files[0].name


def test_cache_write_does_not_leave_temp_files(tmp_path):
    cfg = _cfg(tmp_path)
    _write_cache(cfg, "x", _FEED)
    assert not list(cfg.cache_dir.glob(".feed-*"))


# --- 429 отдаёт кэш ---------------------------------------------------------


def test_429_falls_back_to_cache(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _write_cache(cfg, "Reddit/movies", _FEED)

    class _Resp:
        status_code = 429
        headers = {"x-ratelimit-reset": "1"}

    def fake_get(url, **kwargs):
        err = requests.HTTPError(response=_Resp())
        err.response = _Resp()
        raise err

    monkeypatch.setattr("kino.collect.requests.get", fake_get)
    name, items, error = _fetch_cached(_source(), cfg)
    assert error is None
    assert items and items[0].title == "Dune 3"


def test_429_without_cache_still_reports_error(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)

    class _Resp:
        status_code = 429
        headers = {"x-ratelimit-reset": "1"}

    def fake_get(url, **kwargs):
        err = requests.HTTPError(response=_Resp())
        err.response = _Resp()
        raise err

    monkeypatch.setattr("kino.collect.requests.get", fake_get)
    _, items, error = _fetch_cached(_source(), cfg)
    assert items is None
    assert error and "429" in error


def test_non_429_error_does_not_use_cache(tmp_path, monkeypatch):
    """Кэш спасает только от лимита: 404 — это сломанный фид, не лимит."""
    cfg = _cfg(tmp_path)
    _write_cache(cfg, "Reddit/movies", _FEED)

    class _Resp:
        status_code = 404
        headers: dict = {}

    def fake_get(url, **kwargs):
        err = requests.HTTPError(response=_Resp())
        err.response = _Resp()
        raise err

    monkeypatch.setattr("kino.collect.requests.get", fake_get)
    _, items, error = _fetch_cached(_source(), cfg)
    assert items is None
    assert error == "http error: 404"


def test_successful_fetch_updates_cache(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)

    class _Ok:
        status_code = 200
        content = _FEED

        def raise_for_status(self):
            return None

    monkeypatch.setattr("kino.collect.requests.get", lambda url, **kw: _Ok())
    _fetch_cached(_source(), cfg)
    assert _read_cache(cfg, "Reddit/movies") == _FEED


def test_corrupt_cache_is_ignored(tmp_path, monkeypatch):
    """Битый кэш не должен выдаваться за фид."""
    cfg = _cfg(tmp_path)
    _write_cache(cfg, "Reddit/movies", "<html>не фид</html>".encode("utf-8"))

    class _Resp:
        status_code = 429
        headers = {"x-ratelimit-reset": "1"}

    def fake_get(url, **kwargs):
        err = requests.HTTPError(response=_Resp())
        err.response = _Resp()
        raise err

    monkeypatch.setattr("kino.collect.requests.get", fake_get)
    _, items, error = _fetch_cached(_source(), cfg)
    assert items is None
    assert error and "429" in error


# --- кэш не ломает обычный сбор ---------------------------------------------


def test_collect_uses_cache_for_reddit(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, reddit_pause=0.0)
    cfg.sources = [_source()]
    _write_cache(cfg, "Reddit/movies", _FEED)

    class _Resp:
        status_code = 429
        headers = {"x-ratelimit-reset": "1"}

    def fake_get(url, **kwargs):
        err = requests.HTTPError(response=_Resp())
        err.response = _Resp()
        raise err

    monkeypatch.setattr("kino.collect.requests.get", fake_get)
    monkeypatch.setattr("kino.collect.time.sleep", lambda s: None)
    result = collect(cfg)
    assert result.total == 1
    assert result.broken == []