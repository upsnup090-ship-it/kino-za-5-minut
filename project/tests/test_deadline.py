"""Тесты бюджета времени: остаток, истечение, slice, режим без лимита."""

from __future__ import annotations

from kino.deadline import Deadline, check


def test_none_budget_is_unlimited():
    d = Deadline(None)
    assert d.total == float("inf")
    assert d.expired is False
    assert d.slice(30) == 30


def test_zero_budget_is_unlimited():
    d = Deadline(0)
    assert d.total == float("inf")
    assert d.expired is False


def test_negative_budget_is_unlimited():
    d = Deadline(-5)
    assert d.total == float("inf")


def test_positive_budget_has_remaining():
    d = Deadline(60)
    assert d.total == 60.0
    assert d.remaining > 0
    assert d.expired is False


def test_slice_returns_min_of_want_and_remaining(monkeypatch):
    d = Deadline(60)
    # Остаток большой — slice возвращает запрошенное.
    assert d.slice(10) == 10
    # Остаток маленький — slice возвращает остаток, а не want.
    monkeypatch.setattr(
        "kino.deadline.time.monotonic",
        lambda: d.started + 58,
    )
    assert d.slice(10) == 2


def test_expired_when_budget_spent(monkeypatch):
    d = Deadline(60)
    assert d.expired is False
    monkeypatch.setattr("kino.deadline.time.monotonic", lambda: d.started + 61)
    assert d.expired is True
    assert d.remaining == 0


def test_remaining_never_negative(monkeypatch):
    d = Deadline(5)
    monkeypatch.setattr("kino.deadline.time.monotonic", lambda: d.started + 100)
    assert d.remaining == 0
    assert d.expired is True


def test_elapsed_tracks_time(monkeypatch):
    d = Deadline(100)
    monkeypatch.setattr("kino.deadline.time.monotonic", lambda: d.started + 37)
    assert d.elapsed == 37
    assert d.remaining == 63


def test_check_none_returns_true():
    assert check(None, step="сбор") is True


def test_check_unexpired_returns_true():
    d = Deadline(60)
    assert check(d, step="сбор") is True


def test_check_expired_returns_false(monkeypatch):
    d = Deadline(60)
    monkeypatch.setattr("kino.deadline.time.monotonic", lambda: d.started + 61)
    assert check(d, step="сбор") is False


def test_check_after_wait_expires(monkeypatch):
    """Пауза между попытками может исчерпать оставшийся бюджет."""
    d = Deadline(10)
    assert check(d, step="попытка 1") is True
    monkeypatch.setattr("kino.deadline.time.monotonic", lambda: d.started + 11)
    assert check(d, step="попытка 2") is False