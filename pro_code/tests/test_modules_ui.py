"""Тесты клиентских модулей меню: repeating, stats, backup, кнопки меню.

Baseline §5 (клиентские модули free): повторяющиеся сообщения, статистика,
бэкап — функционал из project_ruch подключён к боту клиента вместо заглушек.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from pro_code import backup_ui, dispatcher, repeating_ui, stats_ui
from pro_code.config import Config
from pro_code.router import build_main_menu, menu_keyboard
from project_ruch.ruch_db import RuchDb

TOKEN_OLD = "1111111111:AAH_old_token_aaaaaaaaaaaaaaaaaa"
TOKEN_NEW = "2222222222:AAH_new_token_bbbbbbbbbbbbbbbbbb"


def _cfg(admin_id: int = 2005841409) -> Config:
    return Config(admin_chat_id=admin_id, clients=[], master_token="master:tok")


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
    assert "Шаг 4/4" in ui_send[-1]["text"]
    repeating_ui.handle_add_dialog("T", 1, 1, "—", clients_db, ruch)
    assert "создано" in ui_send[-1]["text"]
    assert "20:50" in ui_send[-1]["text"], "время должно отображаться в подтверждении"

    items = ruch.list_repeating(1)
    assert len(items) == 1
    assert items[0]["text"] == "Привет мир"
    assert items[0]["time"] == "20:50"
    assert items[0]["days_week"] == "0,2,4"
    assert items[0]["interval_hours"] == 24
    assert not items[0]["pin"] and not items[0]["delete_last"]


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


# --- ДЕЙСТВИЕ 1: дедуп и окно ---------------------------------------------------

def test_due_now_dedup_same_minute(clients_db, ruch, monkeypatch):
    """Два тика в одну минуту → публикация ровно один раз."""
    clients_db.upsert_client(1, plan="free")
    clients_db.add_channel(1, -100111, "Т", "channel", purpose="manual")
    ruch.add_repeating(user_id=1, text="день", time="09:00")
    calls = []
    monkeypatch.setattr(
        "project_ruch.manual_post.publish_post",
        lambda db, pid, tok, **k: calls.append(pid) or {"ok": True, "message_id": 5},
    )
    from datetime import datetime
    from project_ruch.config import RuchConfig
    now = datetime(2026, 11, 5, 9, 0, tzinfo=RuchConfig().timezone)
    r1 = repeating_ui.due_now(ruch, 1, "T", clients_db, now=now)
    r2 = repeating_ui.due_now(ruch, 1, "T", clients_db, now=now)
    assert len(r1) == 1 and r2 == []
    assert len(calls) == 1


def test_due_now_window_after_missed_tick(clients_db, ruch, monkeypatch):
    """Пропущенный тик: время 09:00, тик в 09:07 → пост уходит."""
    clients_db.upsert_client(1, plan="free")
    clients_db.add_channel(1, -100111, "Т", "channel", purpose="manual")
    ruch.add_repeating(user_id=1, text="день", time="09:00")
    calls = []
    monkeypatch.setattr(
        "project_ruch.manual_post.publish_post",
        lambda db, pid, tok, **k: calls.append(pid) or {"ok": True, "message_id": 5},
    )
    from datetime import datetime
    from project_ruch.config import RuchConfig
    now = datetime(2026, 11, 5, 9, 7, tzinfo=RuchConfig().timezone)
    result = repeating_ui.due_now(ruch, 1, "T", clients_db, now=now)
    assert len(result) == 1


def test_due_now_next_day_publishes_again(clients_db, ruch, monkeypatch):
    from datetime import datetime
    from project_ruch.config import RuchConfig
    clients_db.upsert_client(1, plan="free")
    clients_db.add_channel(1, -100111, "Т", "channel", purpose="manual")
    ruch.add_repeating(user_id=1, text="день", time="09:00")
    monkeypatch.setattr(
        "project_ruch.manual_post.publish_post",
        lambda db, pid, tok, **k: {"ok": True, "message_id": 5},
    )
    tz = RuchConfig().timezone
    day1 = datetime(2026, 11, 5, 9, 0, tzinfo=tz)
    day2 = datetime(2026, 11, 6, 9, 0, tzinfo=tz)
    assert len(repeating_ui.due_now(ruch, 1, "T", clients_db, now=day1)) == 1
    assert len(repeating_ui.due_now(ruch, 1, "T", clients_db, now=day2)) == 1


def test_due_now_respects_days_week_filter(clients_db, ruch, monkeypatch):
    """Пн-сообщение не публикуется в четверг даже в своё время."""
    clients_db.upsert_client(1, plan="free")
    clients_db.add_channel(1, -100111, "Т", "channel", purpose="manual")
    ruch.add_repeating(user_id=1, text="день", time="09:00", days_week="0")
    monkeypatch.setattr(
        "project_ruch.manual_post.publish_post",
        lambda *a, **k: {"ok": True, "message_id": 5},
    )
    from datetime import datetime
    from project_ruch.config import RuchConfig
    tz = RuchConfig().timezone
    thursday = datetime(2026, 11, 5, 9, 0, tzinfo=tz)  # 2026-11-05 = чт(3)
    assert repeating_ui.due_now(ruch, 1, "T", clients_db, now=thursday) == []


# --- ДЕЙСТВИЕ 3: опции, pin, delete_last ----------------------------------------

def test_parse_options_full():
    opts = repeating_ui._parse_options("интервал 6, пин, удалять, с 01.11 по 30.11, дни месяца 1,15")
    assert opts["interval_hours"] == 6
    assert opts["pin"] is True
    assert opts["delete_last"] is True
    assert opts["start_date"] == "2026-11-01"
    assert opts["end_date"] == "2026-11-30"
    assert opts["days_month"] == "1,15"


def test_parse_options_skip():
    opts = repeating_ui._parse_options("—")
    assert opts["interval_hours"] == 24 and not opts["pin"]


def test_parse_options_garbage():
    assert repeating_ui._parse_options("абракадабра") is None
    assert repeating_ui._parse_options("интервал , пин") is None


def test_repeat_flow_with_options(clients_db, ruch, ui_send):
    clients_db.upsert_client(1, plan="free")
    clients_db.add_channel(1, -100111, "Т", "channel", purpose="manual")
    repeating_ui.handle("T", 1, 1, "/repeat add", clients_db, ruch)
    for step, val in [("text", "Текст"), ("time", "10:00"), ("days", "ежедневно"), ("opts", "интервал 6, пин")]:
        if step == "text":
            repeating_ui.handle_add_dialog("T", 1, 1, val, clients_db, ruch)
        elif step == "time":
            repeating_ui.handle_add_dialog("T", 1, 1, val, clients_db, ruch)
        elif step == "days":
            repeating_ui.handle_add_dialog("T", 1, 1, val, clients_db, ruch)
        else:
            repeating_ui.handle_add_dialog("T", 1, 1, val, clients_db, ruch)
    assert "создано" in ui_send[-1]["text"]
    item = ruch.list_repeating(1)[0]
    assert item["interval_hours"] == 6
    assert item["pin"] == 1
    assert item["delete_last"] == 0


def test_due_now_delete_last_and_pin(clients_db, ruch, monkeypatch):
    """Второй день: удаляет вчерашний пост (delete_last) и пинает новый."""
    clients_db.upsert_client(1, plan="free")
    clients_db.add_channel(1, -100111, "Т", "channel", purpose="manual")
    ruch.add_repeating(user_id=1, text="день", time="09:00", delete_last=True, pin=True)
    monkeypatch.setattr(
        "project_ruch.manual_post.publish_post",
        lambda db, pid, tok, **k: {"ok": True, "message_id": 100 + pid},
    )
    deleted, pinned = [], []
    monkeypatch.setattr(
        "pro_code.tg_api.delete_message",
        lambda tok, ch, mid: deleted.append((ch, mid)) or True,
    )
    monkeypatch.setattr(
        "pro_code.tg_api.pin_chat_message",
        lambda tok, ch, mid, notify=False: pinned.append((ch, mid)) or True,
    )
    from datetime import datetime
    from project_ruch.config import RuchConfig
    tz = RuchConfig().timezone
    day1 = datetime(2026, 11, 5, 9, 0, tzinfo=tz)
    day2 = datetime(2026, 11, 6, 9, 0, tzinfo=tz)
    r1 = repeating_ui.due_now(ruch, 1, "T", clients_db, now=day1)
    # день 1: удалять нечего
    assert deleted == []
    assert len(pinned) == 1
    r2 = repeating_ui.due_now(ruch, 1, "T", clients_db, now=day2)
    # день 2: удалил вчерашний (message_id=100+post_id первого дня), запинил новый
    assert len(deleted) == 1
    assert len(pinned) == 2
    assert deleted[0][1] == 100 + r1[0]["post_id"]


def test_due_now_failed_publish_still_marks_sent(clients_db, ruch, monkeypatch):
    """Неудачная публикация не ретраится в тот же день."""
    clients_db.upsert_client(1, plan="free")
    clients_db.add_channel(1, -100111, "Т", "channel", purpose="manual")
    ruch.add_repeating(user_id=1, text="день", time="09:00")
    monkeypatch.setattr(
        "project_ruch.manual_post.publish_post",
        lambda db, pid, tok, **k: {"ok": False, "error": "network"},
    )
    from datetime import datetime
    from project_ruch.config import RuchConfig
    now = datetime(2026, 11, 5, 9, 0, tzinfo=RuchConfig().timezone)
    r1 = repeating_ui.due_now(ruch, 1, "T", clients_db, now=now)
    r2 = repeating_ui.due_now(ruch, 1, "T", clients_db, now=now)
    assert r1 and r2 == []


# --- ДЕЙСТВИЕ 4: изоляция диалогов ----------------------------------------------

def test_post_denied_during_repeat_dialog(clients_db, ruch, ui_send):
    from project_ruch import dialog as manual_dialog
    clients_db.upsert_client(1, plan="free")
    clients_db.add_channel(1, -100111, "Т", "channel", purpose="manual")
    repeating_ui.handle("T", 1, 1, "/repeat add", clients_db, ruch)
    assert "Шаг 1/3" in ui_send[-1]["text"]
    # ручной постинг не должен начаться
    started = manual_dialog.handle("T", 1, 1, "/post", clients_db, ruch)
    # dialog.handle обрабатывает /post как команду start — но должен отказать
    # Проверим: state черновика не перезаписан
    state = ruch.get_draft(1)["state"]
    assert state.startswith("repeating:"), f"черновик repeat затёрт: {state}"


def test_repeat_add_denied_during_manual_dialog(clients_db, ruch, ui_send):
    from project_ruch import dialog as manual_dialog
    clients_db.upsert_client(1, plan="free")
    clients_db.add_channel(1, -100111, "Т", "channel", purpose="manual")
    # начать ручной диалог
    manual_dialog.start("T", 1, 1, clients_db, ruch)
    state0 = ruch.get_draft(1)["state"]
    # попытка repeat add — должен отказать и не затирать
    repeating_ui.start_add("T", 1, 1, clients_db, ruch)
    assert ruch.get_draft(1)["state"] == state0


# --- миграция старой БД ----------------------------------------------------------

def test_migration_old_db_without_new_columns(tmp_path):
    """БД старой схемы (без repeating_sent, last_message_id, extra) открывается."""
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE repeating_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, text TEXT,
            time TEXT, interval_hours INTEGER DEFAULT 24, interval_minutes INTEGER DEFAULT 0,
            days_week TEXT, days_month TEXT, start_date TEXT, end_date TEXT,
            pin INTEGER DEFAULT 0, delete_last INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE post_drafts (
            user_id INTEGER PRIMARY KEY, state TEXT, channel_chat_id INTEGER,
            text TEXT, media_type TEXT, media_path TEXT, parse_mode TEXT,
            reply_to_msg_id INTEGER, post_id INTEGER, updated_at TEXT
        );
    """)
    conn.commit(); conn.close()
    db = RuchDb(path)
    db.add_repeating(user_id=1, text="т", time="10:00")
    db.save_draft(1, state="repeating:await_time", extra='{"time":"10:00"}')
    assert db.get_draft(1)["extra"] == '{"time":"10:00"}'
    db.close()


# --- ДЕЙСТВИЕ 5: смена токена — join(15) + продолжение ---------------------------

def test_spawn_bot_dead_thread_does_not_block(monkeypatch, clients_db):
    """Старый поток игнорирует stop → после join(небольшой) новый всё равно стартует.

    (таймаут в коде 15с; здесь подменяем порог через маленький poll_worker-сон,
    проверяем только что spawn не падает и поднимает новый поток.)
    """
    cfg = _cfg()
    clients_db.upsert_client(1, plan="free")
    spawned_tokens = []
    ev = []

    def stubborn_worker(cfg_, client, db_, ruch_, stop=None):
        spawned_tokens.append(client.token)
        e = stop or threading.Event()
        ev.append(e)
        # игнорирует stop первые секунды
        time.sleep(0.3)

    monkeypatch.setattr(dispatcher, "poll_worker", stubborn_worker)
    spawn_bot = dispatcher.make_spawner(cfg, clients_db, None)
    spawn_bot(1, TOKEN_OLD)
    time.sleep(0.05)
    spawn_bot(1, TOKEN_NEW)
    assert TOKEN_NEW in spawned_tokens


# --- ДЕЙСТВИЕ 6: inactive из БД не публикуется -----------------------------------

def test_repeating_tick_skips_inactive(clients_db, monkeypatch):
    """Клиент inactive в БД, токен в .env → тик его не публикует."""
    clients_db.upsert_client(1, plan="free", status="inactive")
    from pro_code.config import ClientBot
    monkeypatch.setattr(
        "pro_code.config._clients_from_env",
        lambda: {1: ClientBot(user_id=1, token=TOKEN_OLD, plan="free", active=True, source="env")},
    )
    called = []
    monkeypatch.setattr(
        "pro_code.repeating_ui.due_now",
        lambda ruch, uid, tok, cdb, now=None: called.append(uid) or [],
    )
    dispatcher._repeating_tick(clients_db)
    assert called == []


def test_repeating_tick_publishes_active(clients_db, monkeypatch):
    clients_db.upsert_client(1, plan="free", status="active")
    clients_db.set_bot_token(1, TOKEN_OLD)
    called = []
    monkeypatch.setattr(
        "pro_code.repeating_ui.due_now",
        lambda ruch, uid, tok, cdb, now=None: called.append(uid) or [],
    )
    dispatcher._repeating_tick(clients_db)
    assert called == [1]
