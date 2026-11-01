"""Тесты обработки `409 Conflict`: второй процесс читает апдейты.

Ситуация реальная: `kino-admin.service` в systemd плюс ручной запуск
`python -m kino admin` на том же `BOT_TOKEN`. Telegram отдаёт 409 на
`getUpdates`, и до этой правки админка повторяла попытку каждые две секунды,
заливая лог одинаковыми строками без единого объяснения.

Проверяется: отдельный тип ошибки, внятный текст, backoff в цикле и то,
что конфликт не путается с обычным сетевым сбоем.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from kino.admin import CONFLICT_BACKOFF_MAX, CONFLICT_BACKOFF_START, run
from kino.config import Config, Settings
from kino.tg_api import TgConflict, TgError, call


def _cfg(tmp_path: Path) -> Config:
    return Config(
        sources=[], weights={"news": 1}, bad=[], sad=[],
        settings=Settings(
            schedule_catchup_minutes=30,
            schedule_late_grace_minutes=20,
            timezone="Europe/Moscow",
            retry_max_attempts=2,
            retry_backoff=0.01,
            telegram_timeout=5,
        ),
        admin_chat_id="-100500",
        bot_token="1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
        state_path=tmp_path / "state.json",
        db_path=tmp_path / "kino.db",
    )


def _response(status: int, *, json_body=None, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.text = text
    if json_body is not None:
        response.json.return_value = json_body
    else:
        response.json.side_effect = ValueError("not json")
    return response


# --- тип ошибки --------------------------------------------------------------


def test_conflict_is_tgerror_subclass():
    """Старый код ловил TgError — конфликт обязан остаться совместимым."""
    assert issubclass(TgConflict, TgError)


def test_conflict_has_default_backoff():
    error = TgConflict("409")

    assert error.retry_after == 60.0


def test_conflict_backoff_is_configurable():
    error = TgConflict("409", retry_after=120.0)

    assert error.retry_after == 120.0


def test_conflict_message_is_sanitized():
    error = TgConflict(
        "getUpdates: https://api.telegram.org/bot1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/getUpdates"
    )

    assert "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw" not in str(error)


# --- call() ------------------------------------------------------------------


def test_call_raises_conflict_on_409(tmp_path):
    cfg = _cfg(tmp_path)
    response = _response(
        409,
        json_body={
            "ok": False,
            "error_code": 409,
            "description": "Conflict: terminated by other getUpdates request",
        },
    )
    with patch("kino.tg_api.requests.post", return_value=response):
        with pytest.raises(TgConflict) as caught:
            call(cfg, "getUpdates", {})

    text = str(caught.value)
    assert "409" in text
    assert "terminated by other getUpdates request" in text
    assert "другой процесс" in text


def test_conflict_message_names_the_fix(tmp_path):
    """Лог должен подсказывать, что смотреть, а не только код ошибки."""
    cfg = _cfg(tmp_path)
    response = _response(409, json_body={"ok": False, "description": "Conflict"})
    with patch("kino.tg_api.requests.post", return_value=response):
        with pytest.raises(TgConflict) as caught:
            call(cfg, "getUpdates", {})

    text = str(caught.value)
    assert "systemd" in text or "ручной" in text


def test_call_handles_409_without_json(tmp_path):
    """Telegram может ответить HTML — падать на разборе тела нельзя."""
    cfg = _cfg(tmp_path)
    response = _response(409, text="<html>Conflict</html>")
    with patch("kino.tg_api.requests.post", return_value=response):
        with pytest.raises(TgConflict) as caught:
            call(cfg, "getUpdates", {})

    assert "409" in str(caught.value)


def test_conflict_is_not_retried_inside_call(tmp_path):
    """409 не повторяем внутри call: повтор не поможет, пока жив второй."""
    cfg = _cfg(tmp_path)
    response = _response(409, json_body={"ok": False, "description": "Conflict"})
    with patch("kino.tg_api.requests.post", return_value=response) as post:
        with pytest.raises(TgConflict):
            call(cfg, "getUpdates", {})

    assert post.call_count == 1


def test_other_http_errors_stay_tgerror(tmp_path):
    """403 — не конфликт: бот заблокирован, но тип другой."""
    cfg = _cfg(tmp_path)
    response = _response(403, text="Forbidden")
    with patch("kino.tg_api.requests.post", return_value=response):
        with pytest.raises(TgError) as caught:
            call(cfg, "getUpdates", {})

    assert not isinstance(caught.value, TgConflict)


def test_network_error_still_retried(tmp_path):
    """Сетевой сбой — это не 409: повторы остаются."""
    cfg = _cfg(tmp_path)
    with patch(
        "kino.tg_api.requests.post",
        side_effect=requests.ConnectionError("boom"),
    ) as post, patch("kino.tg_api.time.sleep"):
        with pytest.raises(TgError) as caught:
            call(cfg, "getUpdates", {})

    assert not isinstance(caught.value, TgConflict)
    assert post.call_count == 2  # retry_max_attempts


# --- run() -------------------------------------------------------------------


def test_run_backs_off_on_conflict(tmp_path):
    """Первый конфликт — пауза CONFLICT_BACKOFF_START, не tick."""
    cfg = _cfg(tmp_path)
    calls = {"n": 0}

    def _poll(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TgConflict("409 Conflict")
        return (None, 0)

    with patch("kino.admin.transport.poll_once", side_effect=_poll), \
         patch("time.sleep"):
        # once=True: один конфликт → выход с кодом 1
        code = run(cfg, once=True)

    assert code == 1


def test_run_reports_conflict_once(tmp_path, caplog):
    """Серия конфликтов не должна печатать ERROR на каждую попытку."""
    import logging

    cfg = _cfg(tmp_path)
    attempts = {"n": 0}

    def _poll(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] <= 3:
            raise TgConflict("409 Conflict")
        return (None, 0)

    with caplog.at_level(logging.ERROR, logger="kino.admin"), \
         patch("kino.admin.transport.poll_once", side_effect=_poll), \
         patch("time.sleep"):
        # once=False → цикл, но обрываем после 4-й попытки
        original = __import__("time").sleep

        def _stop_after(_seconds):
            if attempts["n"] >= 4:
                raise KeyboardInterrupt

        with patch("time.sleep", side_effect=_stop_after):
            try:
                run(cfg, once=False)
            except KeyboardInterrupt:
                pass

    conflict_errors = [r for r in caplog.records if "409" in r.getMessage()]
    assert len(conflict_errors) == 1


def test_run_clears_conflict_state_on_success(tmp_path, caplog):
    """После успешного опроса пишется, что конфликт ушёл."""
    import logging

    cfg = _cfg(tmp_path)
    attempts = {"n": 0}

    def _poll(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise TgConflict("409 Conflict")
        if attempts["n"] == 2:
            return (None, 0)
        raise KeyboardInterrupt

    with caplog.at_level(logging.INFO, logger="kino.admin"), \
         patch("kino.admin.transport.poll_once", side_effect=_poll), \
         patch("time.sleep"):
        try:
            run(cfg, once=False)
        except KeyboardInterrupt:
            pass

    messages = [r.getMessage() for r in caplog.records]
    assert any("409 ушёл" in m for m in messages)


def test_run_backoff_grows_and_caps(tmp_path):
    """Пауза удваивается и упирается в CONFLICT_BACKOFF_MAX."""
    cfg = _cfg(tmp_path)
    sleeps: list[float] = []
    attempts = {"n": 0}

    def _poll(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] > 6:
            raise KeyboardInterrupt
        raise TgConflict("409 Conflict")

    def _sleep(seconds):
        if seconds > 0.001:  # отсекаем tick=2.0 от backoff
            sleeps.append(seconds)

    with patch("kino.admin.transport.poll_once", side_effect=_poll), \
         patch("time.sleep", side_effect=_sleep):
        try:
            run(cfg, once=False)
        except KeyboardInterrupt:
            pass

    assert sleeps[0] == CONFLICT_BACKOFF_START
    assert sleeps[1] == CONFLICT_BACKOFF_START * 2
    assert max(sleeps) <= CONFLICT_BACKOFF_MAX


def test_run_without_admin_chat_id_exits(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.admin_chat_id = ""

    assert run(cfg, once=True) == 1
