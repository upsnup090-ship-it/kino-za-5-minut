"""Тесты sd_notify (шаг 11): протокол, no-op без сокета, интеграция с serve.

Сокет настоящий — unix datagram в tmp_path. Мок здесь проверял бы только
сам мок: протокол sd_notify простой, но ошибиться в нём легко (абстрактный
namespace, формат строк, закрытие сокета).
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from kino import notify


@pytest.fixture
def notifier(tmp_path: Path, monkeypatch):
    """Живой unix-сокет, в который пишет notify, и читалка из него.

    Путь обязан быть коротким: proot-песочница (RikkaHub) ограничивает адрес
    unix-сокета ~15 байтами (на боевом Linux лимит 108, там любой путь ок).
    Поэтому биндим фиксированный короткий путь в /tmp, а не tmp_path;
    тесты не пересекаются, т.к. выполняются последовательно.
    """
    socket_path = "/tmp/n.sock"
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(socket_path)
    server.settimeout(1.0)
    monkeypatch.setenv(notify.NOTIFY_SOCKET_ENV, socket_path)
    notify.reset_for_tests()

    def read() -> str:
        return server.recv(4096).decode("utf-8")

    yield read
    server.close()
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass


@pytest.fixture
def no_socket(monkeypatch):
    monkeypatch.delenv(notify.NOTIFY_SOCKET_ENV, raising=False)
    notify.reset_for_tests()


# --- no-op без systemd -------------------------------------------------------


def test_not_available_without_env(no_socket):
    assert notify.is_available() is False


def test_ready_is_noop_without_env(no_socket):
    assert notify.ready() is False


def test_watchdog_is_noop_without_env(no_socket):
    assert notify.watchdog() is False


def test_status_is_noop_without_env(no_socket):
    assert notify.status("x") is False


def test_stopping_is_noop_without_env(no_socket):
    assert notify.stopping() is False


# --- протокол ----------------------------------------------------------------


def test_available_with_env(notifier):
    assert notify.is_available() is True


def test_ready_sends_ready_line(notifier):
    assert notify.ready() is True
    assert notifier() == "READY=1"


def test_ready_with_status(notifier):
    assert notify.ready("режим slots, слотов 13") is True
    assert notifier() == "READY=1\nSTATUS=режим slots, слотов 13"


def test_ready_sent_once(notifier):
    assert notify.ready() is True
    assert notifier() == "READY=1"
    # Второй вызов — не отправляется вовсе (иначе recv завис бы до таймаута).
    assert notify.ready() is False


def test_watchdog_sends_ping(notifier):
    assert notify.watchdog() is True
    assert notifier() == "WATCHDOG=1"


def test_watchdog_repeats(notifier):
    for _ in range(3):
        assert notify.watchdog() is True
        assert notifier() == "WATCHDOG=1"


def test_status_flattens_newlines(notifier):
    assert notify.status("строка 1\nстрока 2") is True
    assert notifier() == "STATUS=строка 1 строка 2"


def test_status_truncates(notifier):
    notify.status("я" * 500)
    line = notifier()
    assert len(line) < 250


def test_stopping_sends_both_lines(notifier):
    assert notify.stopping("остановка") is True
    assert notifier() == "STOPPING=1\nSTATUS=остановка"


# --- ошибки не пробрасываются -----------------------------------------------


def test_missing_socket_path_returns_false(tmp_path, monkeypatch):
    """Путь есть, сокета нет: не падаем, просто False."""
    monkeypatch.setenv(notify.NOTIFY_SOCKET_ENV, str(tmp_path / "nope.sock"))
    notify.reset_for_tests()
    assert notify.watchdog() is False


def test_garbage_socket_path_returns_false(monkeypatch):
    """Негодный путь (слишком длинный) — False, без исключения."""
    monkeypatch.setenv(notify.NOTIFY_SOCKET_ENV, "/tmp/" + "x" * 300)
    notify.reset_for_tests()
    assert notify.watchdog() is False
    assert notify.is_available() is True  # переменная есть, но сокета нет


def test_null_byte_in_env_does_not_crash(monkeypatch):
    """Окружение с нулевым байтом роняет само чтение — ловим и не падаем.

    `monkeypatch.setenv` такое значение не принимает, поэтому подменяем
    `os.environ.get` — это ровно тот вызов, который бросает ValueError.
    """
    monkeypatch.setenv(notify.NOTIFY_SOCKET_ENV, "/tmp/x.sock")
    notify.reset_for_tests()

    def _boom(key, default=None):
        raise ValueError("embedded null byte")

    monkeypatch.setattr(os.environ.__class__, "get", _boom, raising=False)
    assert notify._send("WATCHDOG=1") is False


# --- интеграция с serve ------------------------------------------------------


def _serve_cfg(tmp_path: Path):
    from kino.config import Config, Settings

    return Config(
        sources=[], weights={"news": 1}, bad=[], sad=[],
        settings=Settings(
            schedule_mode="windows",
            schedule_windows=["11:00-18:00"],
            timezone="Europe/Moscow",
            tick_seconds=60,
            log_file=str(tmp_path / "logs" / "kino.log"),
            log_purge_interval_hours=999,
        ),
        state_path=tmp_path / "state.json",
        db_path=tmp_path / "kino.db",
    )


def test_serve_sends_ready_and_ping(tmp_path, notifier):
    """serve в режиме windows: READY на старте, WATCHDOG на тике.

    pipeline.run замокан: этот тест проверяет sd_notify-протокол, а не сбор.
    Раньше заглушка socket маскировала реальный вызов коллектора — с
    sources=[] он поднимал RuntimeError «сбор: 0 записей» раньше READY.
    """
    from kino.serve import serve
    from unittest.mock import patch
    from kino.pipeline import RunResult

    cfg = _serve_cfg(tmp_path)
    with patch("kino.serve.run", return_value=RunResult(
        caption="", image_url="", format="news",
        published=False, dry_run=True, kind="text",
    )):
        serve(cfg, once=True)

    assert notifier() == "READY=1\nSTATUS=режим windows, окон 1"
    assert notifier() == "WATCHDOG=1"


def test_serve_slots_sends_ready_and_ping(tmp_path, notifier):
    from kino.schedule_db import ScheduleDb
    from kino.serve import serve
    from unittest.mock import patch
    from kino.pipeline import RunResult

    cfg = _serve_cfg(tmp_path)
    cfg.settings.schedule_mode = "slots"
    ScheduleDb(cfg.db_path).add_slot(5, "16:40")

    with patch("kino.serve.run", return_value=RunResult(
        caption="", image_url="", format="news",
        published=False, dry_run=True, kind="text",
    )):
        serve(cfg, once=True)

    assert notifier() == "READY=1\nSTATUS=режим slots, слотов 1"
    assert notifier() == "WATCHDOG=1"


def test_serve_without_systemd_does_not_crash(tmp_path, no_socket):
    from kino.serve import serve
    from unittest.mock import patch
    from kino.pipeline import RunResult

    cfg = _serve_cfg(tmp_path)
    with patch("kino.serve.run", return_value=RunResult(
        caption="", image_url="", format="news",
        published=False, dry_run=True, kind="text",
    )):
        assert serve(cfg, once=True) == 0


def test_stopper_sends_stopping(tmp_path, notifier):
    from kino.serve import Stopper

    stopper = Stopper()
    stopper.request()
    assert notifier() == "STOPPING=1\nSTATUS=остановка после текущего шага"


def test_stopper_without_systemd(no_socket):
    from kino.serve import Stopper

    stopper = Stopper()
    stopper.request()
    assert stopper.stopping is True
