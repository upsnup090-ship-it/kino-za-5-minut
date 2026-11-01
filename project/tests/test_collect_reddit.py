"""Регрессии сбора: лимиты Reddit и разделение очередей.

Найдено боевым прогоном: пять reddit-фидов запускались параллельно, выбирали
окно лимита за один залп, и 4 из 5 получали 429 на каждом прогоне.
"""

from __future__ import annotations

import time

import pytest
import requests

from kino.collect import (
    FeedItem,
    _REDDIT_MAX_WAIT,
    _fetch_one,
    _is_reddit,
    _retry_after_from_error,
    collect,
)
from kino.config import Config, Settings, Source


def _source(name: str, url: str) -> Source:
    return Source(name=name, url=url)


def _cfg(**kwargs) -> Config:
    return Config(
        sources=[],
        settings=Settings(**kwargs),
        llm_base_url="http://llm.test",
        llm_api_key="k",
        bot_token="b",
    )


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Кэш фидов — во временный каталог.

    Без этого тесты писали в настоящий data/feed_cache, и файл от одного
    теста попадал в другой: `_fetch_cached` отдавал кэш и пропускал повторы.
    """
    monkeypatch.setattr(
        "kino.collect._cache_path",
        lambda cfg, name: tmp_path / f"{name.replace('/', '_')}.xml",
    )


# --- определение reddit-источника -------------------------------------------


def test_reddit_host_detected():
    assert _is_reddit(_source("r", "https://www.reddit.com/r/movies/top/.rss")) is True
    assert _is_reddit(_source("r", "https://reddit.com/r/x")) is True
    assert _is_reddit(_source("r", "https://old.reddit.com/r/x")) is True


def test_non_reddit_host_not_detected():
    assert _is_reddit(_source("v", "https://variety.com/feed/")) is False
    assert _is_reddit(_source("d", "https://deadline.com/feed/")) is False
    # Подстрока не считается: notreddit.com — другой хост.
    assert _is_reddit(_source("x", "https://notreddit.com/feed")) is False


# --- разбор окна лимита из текста ошибки ------------------------------------


def test_retry_after_parsed_from_reset():
    assert _retry_after_from_error("http error: 429 (reset=42)", 5.0) == 42.0


def test_retry_after_falls_back_without_number():
    assert _retry_after_from_error("http error: 429 (reset=?)", 5.0) == 5.0
    assert _retry_after_from_error("request error: Timeout", 7.0) == 7.0


# --- 429 повторяется, а не отбрасывается ------------------------------------


def test_429_is_retried(monkeypatch):
    """Раньше 429 не повторялся и фид терялся навсегда."""
    calls: list[int] = []

    class _Resp:
        status_code = 429
        headers = {"x-ratelimit-reset": "1"}

    def fake_get(url, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            err = requests.HTTPError(response=_Resp())
            err.response = _Resp()
            raise err
        # Второй раз отдаём валидный фид.
        class _Ok:
            status_code = 200
            content = (
                b'<?xml version="1.0"?><rss version="2.0"><channel>'
                b"<item><title>Dune 3</title><link>https://e.com/1</link>"
                b"<description>desc</description></item></channel></rss>"
            )

            def raise_for_status(self):
                return None

        return _Ok()

    monkeypatch.setattr("kino.collect.requests.get", fake_get)
    monkeypatch.setattr("kino.collect.time.sleep", lambda s: None)
    name, items, error = _fetch_one(_source("r", "https://www.reddit.com/r/x"), _cfg())
    assert error is None
    assert items and items[0].title == "Dune 3"
    assert len(calls) == 2


def test_non_429_http_error_not_retried(monkeypatch):
    calls: list[int] = []

    class _Resp:
        status_code = 404
        headers: dict = {}

    def fake_get(url, **kwargs):
        calls.append(1)
        err = requests.HTTPError(response=_Resp())
        err.response = _Resp()
        raise err

    monkeypatch.setattr("kino.collect.requests.get", fake_get)
    monkeypatch.setattr("kino.collect.time.sleep", lambda s: None)
    _, _, error = _fetch_one(_source("v", "https://variety.com/feed"), _cfg())
    assert error == "http error: 404"
    assert len(calls) == 1


def test_429_wait_is_capped(monkeypatch):
    """reset у Reddit бывает до минуты — прогон не должен виснуть так долго."""
    waits: list[float] = []

    class _Resp:
        status_code = 429
        headers = {"x-ratelimit-reset": "600"}

    def fake_get(url, **kwargs):
        err = requests.HTTPError(response=_Resp())
        err.response = _Resp()
        raise err

    monkeypatch.setattr("kino.collect.requests.get", fake_get)
    monkeypatch.setattr("kino.collect.time.sleep", lambda s: waits.append(s))
    _fetch_one(_source("r", "https://www.reddit.com/r/x"), _cfg())
    assert waits, "повторы должны были случиться"
    assert all(w <= _REDDIT_MAX_WAIT for w in waits), f"пауза не ограничена: {waits}"


# --- reddit идёт отдельной очередью -----------------------------------------


def test_collect_splits_reddit_from_others(monkeypatch):
    """Reddit-фиды не должны попадать в общий параллельный пул."""
    order: list[str] = []

    def fake_fetch_one(source, cfg, *, deadline=None):
        order.append(source.name)
        # Возвращаем хотя бы одну запись: collect() падает на нуле записей.
        item = FeedItem(
            source=source.name, title="t", link=f"https://e.com/{source.name}",
            pub_date="", ts=1.0, snippet="s",
        )
        return source.name, [item], None

    monkeypatch.setattr("kino.collect._fetch_one", fake_fetch_one)
    monkeypatch.setattr("kino.collect.time.sleep", lambda s: None)

    cfg = _cfg(reddit_pause=0.0)
    cfg.sources = [
        _source("Variety", "https://variety.com/feed"),
        _source("Reddit/movies", "https://www.reddit.com/r/movies/.rss"),
        _source("Reddit/boxoffice", "https://www.reddit.com/r/boxoffice/.rss"),
    ]
    collect(cfg)
    # Все три источника опрошены, reddit — последовательно в конце.
    assert "Reddit/movies" in order and "Reddit/boxoffice" in order
    assert order.index("Reddit/movies") < order.index("Reddit/boxoffice")


def test_reddit_pause_respected(monkeypatch):
    """Между reddit-фидами должна быть пауза."""
    sleeps: list[float] = []

    def fake_fetch_one(source, cfg, *, deadline=None):
        item = FeedItem(
            source=source.name, title="t", link=f"https://e.com/{source.name}",
            pub_date="", ts=1.0, snippet="s",
        )
        return source.name, [item], None

    monkeypatch.setattr("kino.collect._fetch_one", fake_fetch_one)
    monkeypatch.setattr("kino.collect.time.sleep", lambda s: sleeps.append(s))

    cfg = _cfg(reddit_pause=3.0)
    cfg.sources = [
        _source("Reddit/a", "https://www.reddit.com/r/a/.rss"),
        _source("Reddit/b", "https://www.reddit.com/r/b/.rss"),
        _source("Reddit/c", "https://www.reddit.com/r/c/.rss"),
    ]
    collect(cfg)
    # Два промежутка между тремя фидами.
    assert sleeps.count(3.0) == 2, f"ожидалось 2 паузы, получено {sleeps}"