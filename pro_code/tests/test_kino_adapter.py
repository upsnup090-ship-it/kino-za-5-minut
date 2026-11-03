"""Регресс на P0-4: адаптер Kino — публикация клиенту, а не мастеру.

Что проверяем (по дефектам аудита):

1. ``pipeline.run`` вызывается с ``publisher`` — генерация идёт как обычно,
   а публикует адаптер клиентским токеном.
2. Публикация получает **настоящий** конфиг Kino, а не ``None`` (раньше
   ``cfg=None`` ронял публикацию: ``'NoneType' has no attribute 'settings'``).
3. Один прогон → все каналы клиента (а не N прогонов LLM).
4. Дедуп ссылок per-client: уже вышедшую ссылку не публикуем повторно.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# kino живёт в project/ — точка входа диспетчера делает то же самое.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from pro_code.bootstrap import ensure_kino_importable  # noqa: E402

ensure_kino_importable()

from pro_code.kino_auto_db import KinoAutoDb  # noqa: E402
from pro_code.kino_adapter import make_client_publisher, run_auto_post  # noqa: E402


class _Result:
    """Минимальный RunResult для теста."""

    def __init__(self, published: bool = True, format: str = "photo") -> None:
        self.published = published
        self.format = format
        self.kind = "photo"
        self.link = "https://example.com/news"
        self.verdict = "accept"


@pytest.fixture
def auto_db(tmp_path):
    db = KinoAutoDb(tmp_path / "kino_auto.db")
    yield db
    db.close_all()


def _fake_run(result, captured: list):
    """Подменный pipeline.run: запоминает, с каким publisher его позвали."""

    def fake(cfg, dry_run=True, *, deadline=None, publisher=None):
        captured.append({"cfg": cfg, "publisher": publisher, "dry_run": dry_run})
        if publisher is not None:
            cfg_used = cfg
            publisher(
                cfg_used,
                "Подпись поста",
                "http://img",
                False,
                kind="photo",
                link_url=result.link,
            )
        return result

    return fake


def test_publisher_receives_real_cfg_not_none(auto_db):
    """Главный баг: cfg=None ронял публикацию."""
    sent = []

    class _Cfg:
        settings = object()

    def fake_publish(cfg, caption, image_url, dry_run, **kwargs):
        sent.append({"cfg": cfg, "bot_token": kwargs.get("bot_token"),
                     "channel": kwargs.get("channel_chat_id")})
        return True

    with patch("kino.publish.publish", side_effect=fake_publish):
        publisher = make_client_publisher("TOKEN1", [-1001], 5, auto_db)
        cfg = _Cfg()
        assert publisher(cfg, "текст", "", False, link_url="") is True

    assert sent, "публикация не вызвана"
    assert sent[0]["cfg"] is not None, "cfg=None — баг вернулся"
    assert sent[0]["bot_token"] == "TOKEN1"
    assert sent[0]["channel"] == "-1001"


def test_one_run_publishes_to_all_channels(auto_db):
    """Один прогон — публикация во все каналы клиента."""
    channels = [-1001, -1002, -1003]
    published_to = []

    def fake_publish(cfg, caption, image_url, dry_run, **kwargs):
        published_to.append(kwargs.get("channel_chat_id"))
        return True

    with patch("kino.publish.publish", side_effect=fake_publish):
        publisher = make_client_publisher("TOKEN", channels, 5, auto_db)
        publisher(object(), "текст", "", False, link_url="")

    assert published_to == ["-1001", "-1002", "-1003"]


def test_run_auto_post_passes_publisher(auto_db):
    """run_auto_post отдаёт publisher в pipeline и не публикует сам."""
    captured: list = []
    with patch("kino.pipeline.run", side_effect=_fake_run(_Result(), captured)):
        with patch("kino.publish.publish", return_value=True):
            result = run_auto_post(
                client_token="TOKEN", channels=[-1001], user_id=5,
                kino_cfg=object(), auto_db=auto_db,
            )

    assert captured and captured[0]["publisher"] is not None
    assert result["published"] is True


def test_dedup_skips_already_published_link(auto_db):
    """Ссылка, уже ушедшая клиенту, повторно не публикуется."""
    auto_db.mark_link(5, "https://example.com/news")
    called = []

    with patch("kino.publish.publish",
               side_effect=lambda *a, **k: called.append(1) or True):
        publisher = make_client_publisher("TOKEN", [-1001], 5, auto_db)
        ok = publisher(object(), "текст", "", False,
                       link_url="https://example.com/news")

    assert ok is False
    assert called == [], "дубль ссылки всё же опубликован"


def test_dedup_is_per_client(auto_db):
    """У другого клиента та же ссылка — не дубль."""
    auto_db.mark_link(5, "https://example.com/news")
    called = []

    with patch("kino.publish.publish",
               side_effect=lambda *a, **k: called.append(1) or True):
        publisher = make_client_publisher("TOKEN", [-1001], 6, auto_db)
        publisher(object(), "текст", "", False, link_url="https://example.com/news")

    assert called, "для другого клиента публикация должна пройти"


def test_link_marked_only_after_success(auto_db):
    """Неуспешная публикация не помечает ссылку использованной."""
    with patch("kino.publish.publish", return_value=False):
        publisher = make_client_publisher("TOKEN", [-1001], 5, auto_db)
        publisher(object(), "текст", "", False, link_url="https://example.com/x")

    assert auto_db.has_link(5, "https://example.com/x") is False


def test_one_bad_channel_does_not_stop_others(auto_db):
    """Сбой в одном канале не мешает опубликовать в остальные."""
    published_to = []

    def fake_publish(cfg, caption, image_url, dry_run, **kwargs):
        ch = kwargs.get("channel_chat_id")
        if ch == "-1002":
            raise RuntimeError("бот не в канале")
        published_to.append(ch)
        return True

    with patch("kino.publish.publish", side_effect=fake_publish):
        publisher = make_client_publisher("TOKEN", [-1001, -1002, -1003], 5, auto_db)
        ok = publisher(object(), "текст", "", False, link_url="")

    assert ok is True
    assert published_to == ["-1001", "-1003"]
