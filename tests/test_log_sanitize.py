"""Тесты санитизации логов: токен Bot API не должен попадать в файл.

Дыра, которую это закрывает, не в явных `logger.info("...token...")` — таких
нет. Токен приносят `requests`: `ConnectionError` и `Timeout` содержат полный
URL в `str(exc)`, а `logger.exception` печатает traceback с этими строками.
Поэтому проверяется именно путь «упавший запрос → traceback → файл лога».
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from kino.logging_util import REDACTED, SecretFilter, sanitize, setup_logging

#: Реалистичный токен: `<digits>:<base64url>`.
TOKEN = "1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"


# --- sanitize ----------------------------------------------------------------


def test_sanitize_removes_token_from_url():
    cleaned = sanitize(URL)

    assert TOKEN not in cleaned
    assert REDACTED in cleaned
    assert "api.telegram.org" in cleaned


def test_sanitize_keeps_rest_of_message():
    cleaned = sanitize(f"ConnectionError: {URL}: Max retries exceeded")

    assert "ConnectionError" in cleaned
    assert "Max retries exceeded" in cleaned
    assert TOKEN not in cleaned


def test_sanitize_is_idempotent():
    once = sanitize(URL)
    twice = sanitize(once)

    assert once == twice


def test_sanitize_handles_empty_and_plain():
    assert sanitize("") == ""
    assert sanitize("обычный текст без токена") == "обычный текст без токена"


def test_sanitize_multiple_occurrences():
    text = f"первый {URL}, второй {URL}"
    cleaned = sanitize(text)

    assert TOKEN not in cleaned
    assert cleaned.count(REDACTED) == 2


# --- SecretFilter ------------------------------------------------------------


def _record(msg: str, *, args=None, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord(
        name="kino.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=args, exc_info=exc_info,
    )


def test_filter_cleans_message():
    record = _record(f"не доставлено: {URL}")
    SecretFilter().filter(record)

    assert TOKEN not in record.getMessage()


def test_filter_cleans_args():
    record = _record("не доставлено: %s", args=(URL,))
    SecretFilter().filter(record)

    assert TOKEN not in record.getMessage()


def test_filter_cleans_mapping_args():
    """`logger.warning("... %(url)s", {"url": ...})` — mapping в args[0]."""
    record = _record("не доставлено: %(url)s", args=({"url": URL},))
    SecretFilter().filter(record)

    assert TOKEN not in record.getMessage()


def test_filter_cleans_traceback():
    """Главный кейс: requests бросил, traceback содержит URL с токеном."""
    try:
        raise ConnectionError(f"HTTPSConnectionPool(host='api.telegram.org'): {URL}")
    except ConnectionError:
        import sys

        record = _record("публикация упала", exc_info=sys.exc_info())

    SecretFilter().filter(record)

    assert record.exc_text is not None
    assert TOKEN not in record.exc_text
    assert "ConnectionError" in record.exc_text


def test_filter_never_raises():
    """Фильтр не имеет права ронять логирование на странной записи."""
    record = _record("текст")
    record.args = object()  # не tuple и не dict

    assert SecretFilter().filter(record) is True


# --- setup_logging + файл ----------------------------------------------------


def test_setup_logging_installs_filter_on_handlers(tmp_path: Path):
    log_file = tmp_path / "kino.log"
    setup_logging(file=log_file, console=False)

    root = logging.getLogger()
    assert root.handlers
    assert all(any(isinstance(f, SecretFilter) for f in h.filters) for h in root.handlers)

    logging.getLogger("kino.test").warning("не доставлено: %s", URL)
    for handler in root.handlers:
        handler.flush()

    content = log_file.read_text(encoding="utf-8")
    assert TOKEN not in content
    assert REDACTED in content


def test_token_not_in_file_after_exception(tmp_path: Path):
    """Сквозной: упавший requests → logger.exception → файл без токена."""
    log_file = tmp_path / "kino.log"
    setup_logging(file=log_file, console=False)

    try:
        raise ConnectionError(f"Max retries exceeded with url: {URL}")
    except ConnectionError:
        logging.getLogger("kino.publish").exception("публикация упала")

    for handler in logging.getLogger().handlers:
        handler.flush()

    content = log_file.read_text(encoding="utf-8")
    assert TOKEN not in content
    assert "публикация упала" in content
    assert "ConnectionError" in content


# --- исключения --------------------------------------------------------------


def test_tgerror_sanitizes_message():
    from kino.tg_api import TgError

    error = TgError(f"sendMessage: {URL}")

    assert TOKEN not in str(error)
    assert REDACTED in str(error)


def test_publisherror_sanitizes_message():
    from kino.publish import PublishError

    error = PublishError(f"не удалось: {URL}")

    assert TOKEN not in str(error)
    assert REDACTED in str(error)


def test_publisherror_plain_message_unchanged():
    from kino.publish import PublishError

    error = PublishError("BOT_TOKEN не задан: публикация в Telegram невозможна")

    assert str(error) == "BOT_TOKEN не задан: публикация в Telegram невозможна"


@pytest.fixture(autouse=True)
def _restore_logging():
    """setup_logging меняет root-логгер глобально — возвращаем как было."""
    root = logging.getLogger()
    saved = list(root.handlers)
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass
    for handler in saved:
        root.addHandler(handler)