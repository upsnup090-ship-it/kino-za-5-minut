"""Регресс на P0-3: токен не утекает в логи и тексты ошибок.

В аудите токен был виден целиком: сетевой сбой в ``tg_api`` клал в текст
исключения полный URL ``/bot<TOKEN>/getMe``, а диспетчер логировал это
исключение и вдобавок печатал ``token[:12]``.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import requests

from pro_code import tg_api
from pro_code.crypto import mask
from pro_code.dispatcher import verify_bot
from pro_code.sanitize import REDACTED, SecretFilter, sanitize

TOKEN = "123456789:AAHsecretTOKENvalue_0123456789abcdef"
PART = "AAHsecretTOKENvalue"


def test_sanitize_removes_bot_url():
    text = (f"HTTPSConnectionPool(host='api.telegram.org'): Max retries exceeded "
            f"with url: /bot{TOKEN}/getMe")
    cleaned = sanitize(text)
    assert TOKEN not in cleaned
    assert PART not in cleaned
    assert REDACTED in cleaned


def test_sanitize_removes_bare_token():
    cleaned = sanitize(f"токен = {TOKEN}")
    assert TOKEN not in cleaned
    assert PART not in cleaned


def test_sanitize_explicit_token_uses_mask():
    """Если токен известен, он заменяется маской, даже без общего шаблона."""
    cleaned = sanitize(f"видел {TOKEN} тут", token=TOKEN)
    assert TOKEN not in cleaned
    assert mask(TOKEN) in cleaned


def test_sanitize_idempotent():
    once = sanitize(f"/bot{TOKEN}/getMe")
    assert sanitize(once) == once


def test_sanitize_empty():
    assert sanitize("") == ""
    assert sanitize("обычный текст") == "обычный текст"


def test_tg_error_hides_token_on_network_failure():
    """Главный сценарий аудита: сетевой сбой не несёт токен в текст."""

    def boom(*a, **k):
        raise requests.ConnectionError(
            f"Max retries exceeded with url: /bot{TOKEN}/getMe"
        )

    with patch.object(tg_api.requests, "post", side_effect=boom):
        try:
            tg_api.get_me(TOKEN)
        except tg_api.TgError as exc:
            message = str(exc)
        else:  # pragma: no cover - должно бросать
            raise AssertionError("TgError не выброшен")

    assert TOKEN not in message
    assert PART not in message


def test_verify_bot_logs_mask_not_token(caplog):
    """Диспетчер логирует маску, а не первые 12 символов токена."""

    def boom(*a, **k):
        raise requests.ConnectionError("нет сети")

    with patch.object(tg_api.requests, "post", side_effect=boom), \
         caplog.at_level(logging.WARNING, logger="pro_code.dispatcher"):
        assert verify_bot(TOKEN) is False

    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert TOKEN not in joined
    assert PART not in joined
    # Первые 12 символов — то, что печаталось раньше как «почти безопасное».
    assert TOKEN[:12] not in joined


def test_secret_filter_cleans_record():
    """Фильтр вычищает токен из записи до форматирования."""
    filt = SecretFilter()
    record = logging.LogRecord(
        name="x", level=logging.INFO, pathname=__file__, lineno=1,
        msg=f"ошибка: /bot{TOKEN}/sendMessage", args=(), exc_info=None,
    )
    assert filt.filter(record) is True
    assert TOKEN not in record.getMessage()


def test_secret_filter_cleans_args():
    filt = SecretFilter()
    record = logging.LogRecord(
        name="x", level=logging.INFO, pathname=__file__, lineno=1,
        msg="token=%s", args=(TOKEN,), exc_info=None,
    )
    filt.filter(record)
    assert TOKEN not in record.getMessage()
