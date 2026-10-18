"""Регрессии логирования: ротация, автоочистка по возрасту и размеру.

Автоочистка — обязательное требование: каталог логов не должен расти до
заполнения диска на VPS. Два механизма: возраст (редко-но-долго) и размер
(всплеск-за-час).
"""

from __future__ import annotations

import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from kino.logging_util import purge_old_logs, setup_logging


def _make_log_file(dir_path: Path, name: str, size: int = 100, age_days: float = 0.0) -> Path:
    path = dir_path / name
    path.write_bytes(b"x" * size)
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))
    return path


# --- purge_old_logs: по возрасту ---------------------------------------------


def test_purge_removes_old_logs(tmp_path):
    _make_log_file(tmp_path, "old.log", age_days=20)
    _make_log_file(tmp_path, "fresh.log", age_days=1)
    removed = purge_old_logs(tmp_path, retention_days=14)
    assert "old.log" in [p.name for p in removed]
    assert not (tmp_path / "old.log").exists()
    assert (tmp_path / "fresh.log").exists()


def test_purge_keeps_files_within_retention(tmp_path):
    _make_log_file(tmp_path, "a.log", age_days=10)
    _make_log_file(tmp_path, "b.log", age_days=13)
    removed = purge_old_logs(tmp_path, retention_days=14)
    assert removed == []
    assert (tmp_path / "a.log").exists()
    assert (tmp_path / "b.log").exists()


def test_purge_removes_rotated_files(tmp_path):
    """Ротированные версии (kino.log.1, kino.log.2) тоже чистятся по возрасту."""
    _make_log_file(tmp_path, "kino.log.1", age_days=20)
    _make_log_file(tmp_path, "kino.log.2", age_days=30)
    removed = purge_old_logs(tmp_path, retention_days=14)
    assert "kino.log.1" in [p.name for p in removed]
    assert "kino.log.2" in [p.name for p in removed]


# --- purge_old_logs: активный файл не трогаем --------------------------------


def test_purge_never_removes_active_log(tmp_path):
    _make_log_file(tmp_path, "kino.log", age_days=100)
    removed = purge_old_logs(tmp_path, retention_days=14, keep_names=("kino.log",))
    assert removed == []
    assert (tmp_path / "kino.log").exists()


def test_purge_removes_other_active_logs_by_default(tmp_path):
    """Без keep_names активный файл считается обычным и чистится."""
    _make_log_file(tmp_path, "kino.log", age_days=100)
    removed = purge_old_logs(tmp_path, retention_days=14, keep_names=())
    assert "kino.log" in [p.name for p in removed]


# --- purge_old_logs: по размеру ----------------------------------------------


def test_purge_compresses_over_limit_by_age(tmp_path):
    """Раздутый каталог ужимается: удаляются самые старые."""
    _make_log_file(tmp_path, "a.log", size=4000, age_days=10)
    _make_log_file(tmp_path, "b.log", size=4000, age_days=5)
    _make_log_file(tmp_path, "c.log", size=4000, age_days=1)
    removed = purge_old_logs(tmp_path, retention_days=365, max_total_bytes=8000)
    names = [p.name for p in removed]
    assert "a.log" in names  # самый старый удалён
    # После сжатия каталог в пределах лимита.
    total = sum(p.stat().st_size for p in tmp_path.iterdir() if p.is_file())
    assert total <= 8000


def test_purge_keeps_under_limit_untouched(tmp_path):
    _make_log_file(tmp_path, "a.log", size=100)
    _make_log_file(tmp_path, "b.log", size=100)
    removed = purge_old_logs(tmp_path, retention_days=365, max_total_bytes=1000)
    assert removed == []
    assert (tmp_path / "a.log").exists()
    assert (tmp_path / "b.log").exists()


def test_purge_returns_freed_bytes_logged(tmp_path, caplog):
    import logging as _logging
    _make_log_file(tmp_path, "old.log", size=2048, age_days=20)
    with caplog.at_level(_logging.INFO, logger="kino.logging_util"):
        purge_old_logs(tmp_path, retention_days=14)
    assert any("автоочистка" in r.message for r in caplog.records)


def test_purge_missing_dir_is_noop(tmp_path):
    removed = purge_old_logs(tmp_path / "nope", retention_days=14)
    assert removed == []


# --- purge_old_logs: не .log файлы не трогаем --------------------------------


def test_purge_ignores_non_log_files(tmp_path):
    _make_log_file(tmp_path, "data.txt", age_days=30)
    _make_log_file(tmp_path, "state.json", age_days=30)
    removed = purge_old_logs(tmp_path, retention_days=14)
    assert removed == []
    assert (tmp_path / "data.txt").exists()
    assert (tmp_path / "state.json").exists()


# --- setup_logging -----------------------------------------------------------


def test_setup_logging_creates_file(tmp_path):
    log_file = tmp_path / "kino.log"
    setup_logging(file=log_file, level="INFO", console=False)
    logger = logging.getLogger("kino.logging_test")
    logger.info("привет из теста")
    handler = logging.getLogger().handlers[-1]
    handler.flush()
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "привет из теста" in content


def test_setup_logging_respects_debug_level(tmp_path):
    log_file = tmp_path / "kino.log"
    setup_logging(file=log_file, level="DEBUG", console=False)
    logging.getLogger("kino.logging_test").debug("отладка")
    logging.getLogger("kino.logging_test").info("инфо")
    handler = logging.getLogger().handlers[-1]
    handler.flush()
    content = log_file.read_text(encoding="utf-8")
    assert "отладка" in content
    assert "инфо" in content


def test_setup_logging_info_suppresses_debug(tmp_path):
    log_file = tmp_path / "kino.log"
    setup_logging(file=log_file, level="INFO", console=False)
    logging.getLogger("kino.logging_test").debug("отладка")
    handler = logging.getLogger().handlers[-1]
    handler.flush()
    content = log_file.read_text(encoding="utf-8")
    assert "отладка" not in content


def test_setup_logging_rotation(tmp_path):
    """При превышении max_bytes появляется ротированная версия."""
    log_file = tmp_path / "kino.log"
    setup_logging(file=log_file, level="DEBUG", console=False, max_bytes=500, backup_count=3)
    logger = logging.getLogger("kino.logging_test")
    for i in range(100):
        logger.info("сообщение номер %d — достаточно длинное чтобы превысить лимит", i)
    handler = logging.getLogger().handlers[-1]
    handler.flush()
    assert log_file.exists()
    # Хотя бы одна ротированная версия.
    rotated = [p for p in tmp_path.iterdir() if p.name.startswith("kino.log.")]
    assert rotated, "ротация не сработала"


def test_setup_logging_clears_previous_handlers(tmp_path):
    """Повторный вызов не дублирует записи (хендлеры сбрасываются)."""
    log_file = tmp_path / "kino.log"
    setup_logging(file=log_file, level="INFO", console=False)
    first_id = id(logging.getLogger().handlers[-1])
    setup_logging(file=log_file, level="INFO", console=False)
    handlers = logging.getLogger().handlers
    assert all(id(h) != first_id for h in handlers)
    # Ровно один файловый хендлер.
    file_handlers = [h for h in handlers if isinstance(h, RotatingFileHandler)]
    assert len(file_handlers) == 1


def test_setup_logging_missing_dir_does_not_crash(tmp_path):
    """Недоступный каталог логов не роняет приложение."""
    log_file = tmp_path / "нет_такого_каталога" / "kino.log"
    # Должен просто предупредить, а не бросить исключение.
    setup_logging(file=log_file, level="INFO", console=False)
    logging.getLogger("kino.logging_test").info("работаем")
    assert True  # добрались сюда без исключения