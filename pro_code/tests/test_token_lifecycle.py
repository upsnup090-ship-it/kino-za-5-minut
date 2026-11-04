"""Тесты жизненного цикла токена: смена токена у живого бота и 401-шторм.

Регресс на баг 04.11.2026: клиент перевыпустил токен, spawn_bot увидел
живой поток и отказался («уже запущен») — polling остался на мёртвом токене
и штормил 401 каждые 3 секунды, бот клиента молчал.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from pro_code import dispatcher
from pro_code.config import Config
from pro_code.tg_api import TgAuthError

TOKEN_OLD = "1111111111:AAH_old_token_aaaaaaaaaaaaaaaaaa"
TOKEN_NEW = "2222222222:AAH_new_token_bbbbbbbbbbbbbbbbbb"


@dataclass
class FakeClient:
    user_id: int = 1
    token: str = TOKEN_OLD
    plan: str = "free"
    active: bool = True
    source: str = "db"
    bot_username: str | None = None


def _cfg(admin_id: int = 2005841409) -> Config:
    return Config(admin_chat_id=admin_id, clients=[], master_token="master:tok")


# --- spawn_bot: смена токена перезапускает поток -------------------------------

class _FakeThread:
    def __init__(self, target=None, args=(), daemon=None, name=None):
        self.target = target
        self.args = args
        self.name = name
        self.started = False
        self._alive = False
        self._real = None  # реальный поток, если решаем запустить по-настоящему

    def start(self):
        self.started = True
        # poll_worker из args[0]; для теста замены токена запускать реально
        self._real = threading.Thread(target=self.target, args=self.args, daemon=True)
        self._real.start()

    def is_alive(self):
        return self._real.is_alive() if self._real else False

    def join(self, timeout=None):
        if self._real:
            self._real.join(timeout=timeout)


def test_spawn_bot_replaces_thread_on_new_token(monkeypatch, clients_db):
    """Смена токена: старый поток остановлен, поднят новый с новым токеном."""
    cfg = _cfg()
    clients_db.upsert_client(1, plan="free")

    live = []  # токены реально поллимых потоков
    stops = []

    def fake_poll_worker(cfg_, client, db_, ruch_, stop=None):
        live.append(client.token)
        ev = stop or threading.Event()
        stops.append(ev)
        while not ev.is_set():
            time.sleep(0.02)

    monkeypatch.setattr(dispatcher, "poll_worker", fake_poll_worker)
    spawn_bot = dispatcher.make_spawner(cfg, clients_db, None)

    spawn_bot(1, TOKEN_OLD)
    assert len(live) == 1 and live[0] == TOKEN_OLD

    # тот же токен — no-op
    spawn_bot(1, TOKEN_OLD)
    assert len(live) == 1

    # новый токен — перезапуск
    spawn_bot(1, TOKEN_NEW)
    assert TOKEN_NEW in live, "новый токен не подхвачен"
    assert stops[0].is_set(), "старый поток не получил stop"
    assert len(live) == 2


def test_spawn_bot_noop_on_same_token(monkeypatch, clients_db):
    cfg = _cfg()
    clients_db.upsert_client(1, plan="free")
    spawned = []
    stopped = []

    def blocking_worker(cfg_, client, db_, ruch_, stop=None):
        spawned.append(client.token)
        ev = stop or threading.Event()
        stopped.append(ev)
        while not ev.is_set():
            time.sleep(0.02)

    monkeypatch.setattr(dispatcher, "poll_worker", blocking_worker)
    spawn_bot = dispatcher.make_spawner(cfg, clients_db, None)
    spawn_bot(1, TOKEN_OLD)
    spawn_bot(1, TOKEN_OLD)
    assert spawned == [TOKEN_OLD]


# --- poll_worker: 401-шторм останавливает поток --------------------------------

class FlappingUpdates:
    """get_updates всегда кидает TgAuthError."""

    def __init__(self, error=None):
        self.error = error or TgAuthError("401 Unauthorized: токен недействителен")
        self.calls = 0

    def __call__(self, token, offset=None):
        self.calls += 1
        raise self.error


def test_poll_worker_stops_after_max_auth_errors(monkeypatch, clients_db):
    """5 подряд 401 → polling остановлен, клиент помечен inactive, админ уведомлён."""
    cfg = _cfg()
    clients_db.upsert_client(1, plan="free")
    flapping = FlappingUpdates()

    monkeypatch.setattr(dispatcher, "get_updates", flapping)
    monkeypatch.setattr(dispatcher, "POLL_BACKOFF", 0)
    monkeypatch.setattr(dispatcher, "MAX_AUTH_ERRORS", 5)

    notified = []
    monkeypatch.setattr(
        dispatcher, "_notify_admin_token_dead",
        lambda c, uid: notified.append(uid),
    )

    stop = threading.Event()
    client = FakeClient(token=TOKEN_OLD)
    t = threading.Thread(
        target=dispatcher.poll_worker,
        args=(cfg, client, clients_db, None, stop),
        daemon=True,
    )
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "поток не остановился после 5×401"
    assert flapping.calls >= 5
    assert clients_db.get_client(1)["status"] == "inactive"
    assert notified == [1]


def test_poll_worker_recovers_auth_errors_on_success(monkeypatch, clients_db):
    """Один 401, потом успех — счётчик сбрасывается, поток продолжает."""
    cfg = _cfg()
    clients_db.upsert_client(1, plan="free")
    calls = {"n": 0}

    def flaky(token, offset=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TgAuthError("401 Unauthorized")
        return []

    monkeypatch.setattr(dispatcher, "get_updates", flaky)
    monkeypatch.setattr(dispatcher, "POLL_BACKOFF", 0)

    stop = threading.Event()
    client = FakeClient(token=TOKEN_OLD)
    t = threading.Thread(
        target=dispatcher.poll_worker,
        args=(cfg, client, clients_db, None, stop),
        daemon=True,
    )
    t.start()
    time.sleep(0.2)
    stop.set()
    t.join(timeout=3)
    assert calls["n"] >= 2, "после 401 поток должен продолжить"
    assert clients_db.get_client(1)["status"] == "active"


def test_poll_worker_stops_on_stop_event(monkeypatch, clients_db):
    """stop_event.set() → поток выходит (смена токена)."""
    cfg = _cfg()
    clients_db.upsert_client(1, plan="free")

    def ok(token, offset=None):
        time.sleep(0.05)
        return []

    monkeypatch.setattr(dispatcher, "get_updates", ok)

    stop = threading.Event()
    client = FakeClient(token=TOKEN_OLD)
    t = threading.Thread(
        target=dispatcher.poll_worker,
        args=(cfg, client, clients_db, None, stop),
        daemon=True,
    )
    t.start()
    stop.set()
    t.join(timeout=3)
    assert not t.is_alive()


def test_notify_admin_token_dead_sends_message(monkeypatch):
    """_notify_admin_token_dead шлёт админу сообщение через master-токен."""
    cfg = _cfg()
    sent = []
    monkeypatch.setattr(
        dispatcher, "send_message",
        lambda tok, chat_id, text, **kw: sent.append((tok, chat_id, text)),
    )
    dispatcher._notify_admin_token_dead(cfg, 8744545308)
    assert len(sent) == 1
    tok, chat_id, text = sent[0]
    assert chat_id == 2005841409
    assert "8744545308" in text
    assert "401" in text


def test_notify_admin_skips_when_admin_is_client(monkeypatch):
    """Клиент сам админ — не спамим самому себе (токен всё равно мёртв)."""
    cfg = _cfg(admin_id=1)
    sent = []
    monkeypatch.setattr(
        dispatcher, "send_message",
        lambda tok, chat_id, text, **kw: sent.append((tok, chat_id, text)),
    )
    dispatcher._notify_admin_token_dead(cfg, 1)
    assert sent == []
