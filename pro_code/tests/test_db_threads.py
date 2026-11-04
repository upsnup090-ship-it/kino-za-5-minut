"""Регресс: БД должна работать из разных потоков.

Диспетчер создаёт объекты БД в главном потоке, а боты работают с ними
каждый в своём. До правки это падало с «SQLite objects created in a thread
can only be used in that same thread» — бот молчал всегда.

Проверяем ровно это: запись и чтение одной БД из нескольких потоков.
"""

from __future__ import annotations

import threading

import pytest

from pro_code.clients_db import ClientsDb
from pro_code.kino_auto_db import KinoAutoDb
from project_ruch.ruch_db import RuchDb
from project_ruch.rezerv_post.rezerv_db import ReserveDb


def _run_in_threads(target, count: int = 3) -> list[BaseException]:
    """Запустить target(i) в count потоках, вернуть исключения."""
    errors: list[BaseException] = []

    def wrapper(i: int) -> None:
        try:
            target(i)
        except BaseException as exc:  # noqa: BLE001 — тест собирает всё
            errors.append(exc)

    threads = [threading.Thread(target=wrapper, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def test_clients_db_across_threads(tmp_path):
    """Клиенты пишутся и читаются из чужих потоков."""
    db = ClientsDb(tmp_path / "clients.db")

    errors = _run_in_threads(
        lambda i: (db.upsert_client(i, plan="free"), db.get_client(i))
    )

    assert errors == [], f"ошибки в потоках: {errors}"
    assert {c["user_id"] for c in db.list_clients()} == {0, 1, 2}
    db.close_all()


def test_ruch_db_across_threads(tmp_path):
    """Ручной модуль работает из нескольких потоков."""
    db = RuchDb(tmp_path / "ruch.db")

    errors = _run_in_threads(
        lambda i: db.save_draft(i, state="await_channel", channel_chat_id=None)
    )

    assert errors == [], f"ошибки в потоках: {errors}"
    assert db.get_draft(1)["state"] == "await_channel"
    db.close_all()


def test_reserve_db_across_threads(tmp_path):
    """Резерв открывается и наполняется из разных потоков."""
    db = ReserveDb(tmp_path / "rezerv.db")

    errors = _run_in_threads(lambda i: db.add(i, f"пост {i}"))

    assert errors == [], f"ошибки в потоках: {errors}"
    assert db.count_pending(2) == 1
    db.close_all()


def test_kino_auto_db_across_threads(tmp_path):
    """БД автопостинга — слоты и резерв из разных потоков."""
    db = KinoAutoDb(tmp_path / "kino_auto.db")

    errors = _run_in_threads(lambda i: db.add_slot(i, i, "09:00"))

    assert errors == [], f"ошибки в потоках: {errors}"
    assert len(db.list_slots(1)) == 1
    db.close_all()


def test_two_handles_same_file(tmp_path):
    """Два объекта на один файл (конфиг + диспетчер) не мешают друг другу."""
    path = tmp_path / "clients.db"
    a = ClientsDb(path)
    b = ClientsDb(path)

    errors = _run_in_threads(lambda i: a.upsert_client(i))
    errors += _run_in_threads(lambda i: b.get_client(i))

    assert errors == [], f"ошибки: {errors}"
    assert b.get_client(0) is not None
    a.close_all()
    b.close_all()


def test_slot_done_counted_after_start_finish(tmp_path):
    """finish_run пишет строку в runs — slot_done это видит.

    Регресс к serve-циклу: раньше finish_run(0) не находил строку, и один
    слот публиковался повторно.
    """
    db = KinoAutoDb(tmp_path / "kino_auto.db")
    run_id = db.start_run(1, 5, "2026-11-02 09:00")
    db.finish_run(run_id, outcome="published")

    assert db.slot_done(1, 5, "2026-11-02 09:00") is True
    db.close_all()
