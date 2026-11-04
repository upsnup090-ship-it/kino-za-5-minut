"""Регрессии блокировки: мёртвый владелец, битый файл, перехват.

Пункт 4 из docs/todo.md.
"""

from __future__ import annotations

import os
import time

from kino.state import StateLock


def _dead_pid() -> int:
    """PID, которого точно нет: заведомо огромный и не наш."""
    candidate = 999_999
    while candidate == os.getpid():
        candidate += 1
    return candidate


def test_lock_acquire_and_release(tmp_path):
    lock = StateLock(tmp_path / "state.lock")
    assert lock.acquire() is True
    lock.release()
    assert not (tmp_path / "state.lock").exists()


def test_second_acquire_blocked_while_held(tmp_path):
    first = StateLock(tmp_path / "state.lock")
    second = StateLock(tmp_path / "state.lock")
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()


def test_lock_reacquired_after_release(tmp_path):
    first = StateLock(tmp_path / "state.lock")
    second = StateLock(tmp_path / "state.lock")
    first.acquire()
    first.release()
    assert second.acquire() is True
    second.release()


def test_stale_lock_from_dead_process_is_taken_over(tmp_path):
    """Главный кейс п.4: процесс умер, файл остался — пайплайн не должен встать."""
    path = tmp_path / "state.lock"
    path.write_text(str(_dead_pid()), encoding="utf-8")
    lock = StateLock(path)
    assert lock.acquire() is True
    lock.release()


def test_lock_from_live_process_is_respected(tmp_path):
    """Свой PID живой — блокировка честно держится."""
    path = tmp_path / "state.lock"
    path.write_text(str(os.getpid()), encoding="utf-8")
    lock = StateLock(path)
    assert lock.acquire() is False


def test_corrupt_lock_file_is_taken_over(tmp_path):
    path = tmp_path / "state.lock"
    path.write_text("не число", encoding="utf-8")
    lock = StateLock(path)
    assert lock.acquire() is True
    lock.release()


def test_empty_lock_file_is_taken_over(tmp_path):
    path = tmp_path / "state.lock"
    path.write_text("", encoding="utf-8")
    lock = StateLock(path)
    assert lock.acquire() is True
    lock.release()


def test_old_lock_is_taken_over_even_if_pid_alive(tmp_path):
    """Второй предохранитель: слишком старый файл считается брошенным."""
    path = tmp_path / "state.lock"
    path.write_text(str(os.getpid()), encoding="utf-8")
    old = time.time() - 7200
    os.utime(path, (old, old))
    lock = StateLock(path, stale_after=3600.0)
    assert lock.acquire() is True
    lock.release()


def test_fresh_lock_with_alive_pid_not_stale(tmp_path):
    """Свежий файл с живым владельцем не перехватывается."""
    path = tmp_path / "state.lock"
    path.write_text(str(os.getpid()), encoding="utf-8")
    lock = StateLock(path, stale_after=3600.0)
    assert lock.acquire() is False