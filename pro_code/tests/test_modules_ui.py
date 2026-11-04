"""Тесты клиентских модулей меню: repeating, stats, backup, кнопки меню.

Baseline §5 (клиентские модули free): повторяющиеся сообщения, статистика,
бэкап — функционал из project_ruch подключён к боту клиента вместо заглушек.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from pro_code import backup_ui, repeating_ui, stats_ui
from pro_code.router import build_main_menu, menu_keyboard
from project_ruch.ruch_db import RuchDb


# --- меню: кнопки по плану ------------------------------------------------------

def test_menu_keyboard_free():
    kb = menu_keyboard("free")
    labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert "🤖 Автопостинг / слоты" not in labels
    assert "🔁 Повторяющиеся" in labels
    assert "📊 Статистика" in labels
    assert "💾 Бэкап" in labels


def test_menu_keyboard_auto_has_autoposting():
    kb = menu_keyboard("auto")
    labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert "🤖 Автопостинг / слоты" in labels


def test_menu_keyboard_owner_has_admin():
    kb = menu_keyboard("free", is_owner=True)
    labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert "🛠 Админ-панель" in labels


def test_build_main_menu_mentions_commands():
    text = build_main_menu("free")
    assert "/repeat add" in text
    assert "/stats" in text
    assert "/backup" in text


# --- repeating: список, создание по диалогу, вкл/выкл/удаление -------------------

@pytest.fixture
def ruch(tmp_path):
    db = RuchDb(tmp_path / "ruch.db")
    yield db
    db.close()


@pytest.fixture
def ui_send(sent, monkeypatch):
    """Мок send_message во всех новых ui-модулях → список sent."""
    def _fake(token, chat_id, text, **kwargs):
        sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return {"message_id": len(sent)}
    for mod in (repeating_ui, stats_ui, backup_ui):
        if hasattr(mod, "send_message"):
            monkeypatch.setattr(mod, "send_message", _fake)
    return sent


def test_repeat_add_dialog_full_flow(clients_db, ruch, ui_send):
    """Диалог /repeat add: текст → время → дни → запись в БД."""
    clients_db.upsert_client(1, plan="free")
    clients_db.add_channel(1, -100111, "Тест", "channel", purpose="manual")

    repeating_ui.handle("T", 1, 1, "/repeat add", clients_db, ruch)
    # шаг 1 — ждали текст
    assert "Шаг 1/3" in ui_send[-1]["text"]
    repeating_ui.handle_add_dialog("T", 1, 1, "Привет мир", clients_db, ruch)
    assert "Шаг 2/3" in ui_send[-1]["text"]
    repeating_ui.handle_add_dialog("T", 1, 1, "20:50", clients_db, ruch)
    assert "Шаг 3/3" in ui_send[-1]["text"]
    repeating_ui.handle_add_dialog("T", 1, 1, "пн,ср,пт", clients_db, ruch)
    assert "создано" in ui_send[-1]["text"]

    items = ruch.list_repeating(1)
    assert len(items) == 1
    assert items[0]["text"] == "Привет мир"
    assert items[0]["time"] == "20:50"
    assert items[0]["days_week"] == "0,2,4"


def test_repeat_add_without_channels_denied(clients_db, ruch, ui_send):
    clients_db.upsert_client(1, plan="free")
    repeating_ui.handle("T", 1, 1, "/repeat add", clients_db, ruch)
    assert "Нет каналов" in ui_send[-1]["text"]
    assert ruch.list_repeating(1) == []


def test_repeat_limit(clients_db, ruch, ui_send, monkeypatch):
    from types import SimpleNamespace
    from project_ruch.config import RuchConfig
    real = RuchConfig()
    # RuchConfig — dataclass: наследование не переопределяет дефолт поля,
    # поэтому подменяем весь класс на заглушку с тем же timezone.
    monkeypatch.setattr(
        "pro_code.repeating_ui.RuchConfig",
        lambda: SimpleNamespace(max_repeating_per_client=1, timezone=real.timezone),
    )
    clients_db.upsert_client(1, plan="free")
    clients_db.add_channel(1, -100111, "Т", "channel", purpose="manual")
    ruch.add_repeating(user_id=1, text="первое", time="10:00")
    repeating_ui.handle("T", 1, 1, "/repeat add", clients_db, ruch)
    assert "лимит" in ui_send[-1]["text"].lower()


def test_repeat_on_off_del(clients_db, ruch, ui_send):
    clients_db.upsert_client(1, plan="free")
    mid = ruch.add_repeating(user_id=1, text="т", time="10:00")
    repeating_ui.handle("T", 1, 1, f"/repeat off {mid}", clients_db, ruch)
    assert not ruch.get_repeating(mid)["enabled"]
    repeating_ui.handle("T", 1, 1, f"/repeat on {mid}", clients_db, ruch)
    assert ruch.get_repeating(mid)["enabled"]
    repeating_ui.handle("T", 1, 1, f"/repeat del {mid}", clients_db, ruch)
    assert ruch.get_repeating(mid) is None


def test_repeat_cannot_touch_foreign(clients_db, ruch, ui_send):
    """Чужой id не трогается: сообщение #2 принадлежит user_id=2."""
    ruch.add_repeating(user_id=2, text="чужое", time="10:00")
    clients_db.upsert_client(1, plan="free")
    repeating_ui.handle("T", 1, 1, "/repeat del 2", clients_db, ruch)
    assert "не найдено" in ui_send[-1]["text"]
    assert ruch.get_repeating(1) is not None


def test_repeat_dialog_cancel(clients_db, ruch, ui_send):
    clients_db.upsert_client(1, plan="free")
    clients_db.add_channel(1, -100111, "Т", "channel", purpose="manual")
    repeating_ui.handle("T", 1, 1, "/repeat add", clients_db, ruch)
    repeating_ui.handle_add_dialog("T", 1, 1, "/cancel", clients_db, ruch)
    assert "Отменено" in ui_send[-1]["text"]
    # после отмены сообщение не поглощается
    assert repeating_ui.handle_add_dialog("T", 1, 1, "текст", clients_db, ruch) is False


# --- repeating: публикация по расписанию ----------------------------------------

def test_due_now_publishes_to_manual_channels(clients_db, ruch, monkeypatch):
    clients_db.upsert_client(1, plan="free")
    clients_db.add_channel(1, -100111, "Т", "channel", purpose="manual")
    ruch.add_repeating(user_id=1, text="ежедневка", time="09:00")

    published = []
    monkeypatch.setattr(
        "project_ruch.manual_post.publish_post",
        lambda db, post_id, token, message_id=None: published.append(post_id) or {"ok": True, "message_id": 42},
    )
    # время 09:00 в зоне клиента
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from project_ruch.config import RuchConfig
    now = datetime(2026, 11, 5, 9, 0, tzinfo=RuchConfig().timezone)
    result = repeating_ui.due_now(ruch, 1, "T", clients_db, now=now)
    assert len(result) == 1 and result[0]["ok"]
    assert published, "пост не публиковался"


def test_due_now_respects_disabled(clients_db, ruch, monkeypatch):
    clients_db.upsert_client(1, plan="free")
    clients_db.add_channel(1, -100111, "Т", "channel", purpose="manual")
    mid = ruch.add_repeating(user_id=1, text="выключено", time="09:00")
    ruch.toggle_repeating(mid)

    monkeypatch.setattr(
        "project_ruch.manual_post.publish_post",
        lambda *a, **k: {"ok": True, "message_id": 1},
    )
    from datetime import datetime
    from project_ruch.config import RuchConfig
    now = datetime(2026, 11, 5, 9, 0, tzinfo=RuchConfig().timezone)
    assert repeating_ui.due_now(ruch, 1, "T", clients_db, now=now) == []


# --- статистика ------------------------------------------------------------------

def test_stats_screen(clients_db, ruch, ui_send):
    clients_db.upsert_client(1, plan="free")
    ruch.add_manual_post(user_id=1, channel_chat_id=-100, text="пост")
    stats_ui.send_stats("T", 1, 1, clients_db)
    text = ui_send[-1]["text"]
    assert "статистика" in text.lower()
    assert "Просмотры" in text


# --- бэкап ------------------------------------------------------------------------

def test_backup_generates_and_sends(clients_db, ruch, ui_send, tmp_path, monkeypatch):
    clients_db.upsert_client(1, plan="free")
    ruch.add_manual_post(user_id=1, channel_chat_id=-100, text="пост")
    monkeypatch.setattr(
        "pro_code.backup_ui.RuchDb", lambda: ruch,
    )
    monkeypatch.setattr(
        "project_ruch.backup.RuchConfig", lambda: type("C", (), {
            "user_backup_dir": tmp_path / "bk",
        })(),
    )
    # generate_user_backup тоже юзает RuchConfig внутри
    sent_files = []
    monkeypatch.setattr(
        "pro_code.backup_ui.send_document",
        lambda tok, ch, path, caption=None: sent_files.append(path),
    )
    backup_ui.handle("T", 1, 1, "/backup")
    assert len(sent_files) == 1
    assert sent_files[0].endswith(".csv")


def test_backup_txt_format(clients_db, ruch, ui_send, tmp_path, monkeypatch):
    monkeypatch.setattr("pro_code.backup_ui.RuchDb", lambda: ruch)
    monkeypatch.setattr(
        "project_ruch.backup.RuchConfig",
        lambda: type("C", (), {"user_backup_dir": tmp_path / "bk"})(),
    )
    sent_files = []
    monkeypatch.setattr(
        "pro_code.backup_ui.send_document",
        lambda tok, ch, path, caption=None: sent_files.append(path),
    )
    backup_ui.handle("T", 1, 1, "/backup txt")
    assert sent_files and sent_files[0].endswith(".txt")
