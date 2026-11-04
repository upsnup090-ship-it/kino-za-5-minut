"""Тесты новой админ-иерархии: /start → приветствие → 3 раздела.

Старый S0-экран удалён: главный экран теперь только приветствие с тремя
кнопками, а вся функциональность живёт внутри раздела «Автопостинг ТГ».
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from kino.admin import handle_callback, screen_home
from kino.config import Config, Settings


def _cfg(tmp_path: Path) -> Config:
    return Config(
        sources=[],
        weights={"news": 1},
        bad=[],
        sad=[],
        settings=Settings(
            schedule_mode="slots",
            schedule_catchup_minutes=30,
            schedule_late_grace_minutes=20,
            timezone="Europe/Moscow",
        ),
        admin_chat_id="-100500",
        state_path=tmp_path / "state.json",
        db_path=tmp_path / "kino.db",
    )


def _now() -> datetime:
    return datetime(2026, 11, 2, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))


def _data(reply) -> list[str]:
    return [btn["callback_data"] for row in reply.keyboard for btn in row]


def test_home_is_greeting_with_three_buttons(tmp_path):
    reply = screen_home(_cfg(tmp_path))

    assert "👋 Приветствие" in reply.text
    assert "Выбери раздел" in reply.text
    assert "Время бота:" in reply.text
    assert _data(reply) == ["nav:autoposting", "nav:about", "nav:backup"]


def test_autoposting_opens_state_screen(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "nav:autoposting", _now())

    assert "🤖 Автопостинг ТГ" in reply.text
    assert "цикл:" in reply.text
    assert "nav:state" in _data(reply)
    assert "nav:state:errors" in _data(reply)
    assert "state:pause" in _data(reply)
    assert "nav:runnow" in _data(reply)
    assert "nav:sched" in _data(reply)
    assert "nav:runs:0" in _data(reply)
    assert "nav:home" in _data(reply)


def test_about_opens_about_screen(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "nav:about", _now())

    assert "ℹ️ О боте" in reply.text
    assert "Кино за 5 минут" in reply.text
    assert _data(reply) == ["nav:home"]


def test_backup_opens_backup_screen(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "nav:backup", _now())

    assert "🗄 Бэкап+логи" in reply.text
    assert "логи" in reply.text.lower()
    assert _data(reply) == ["nav:backup:logs", "nav:backup:make", "nav:home"]


def test_unknown_nav_returns_home(tmp_path):
    reply = handle_callback(_cfg(tmp_path), "nav:nonsense", _now())

    assert "👋 Приветствие" in reply.text
    assert "Выбери раздел" in reply.text
    assert "Время бота:" in reply.text
    assert _data(reply) == ["nav:autoposting", "nav:about", "nav:backup"]
