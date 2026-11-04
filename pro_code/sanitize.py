"""Вычистить токены ботов из текста до логирования и показа.

Зачем отдельный модуль. Токен бота — полный доступ к боту. В лог он попадает
не через явный ``logger.info("...token...")``: его приносят ``requests`` —
``ConnectionError``/``Timeout`` содержат в ``str(exc)`` полный URL вида
``https://api.telegram.org/bot<TOKEN>/getMe``, а ``logger.exception`` печатает
traceback с этими строками. Плюс ``dispatcher`` печатал ``token[:12]`` — это
уже почти весь секрет.

В Kino такой санитайзер есть (``kino.logging_util``), но ``pro_code`` не должен
зависеть от Kino, поэтому здесь свой, с теми же правилами: чистка не мест
вызова, а всего, что уходит в обработчики.
"""

from __future__ import annotations

import logging
import re

from pro_code.crypto import mask as _mask_token

#: Токен Bot API: ``<цифры>:<base64url-подобное>``. Формат не гарантирован,
#: поэтому в первом правиле берём всё до разделителя, во втором — вариант
#: внутри URL ``/bot<...>:<...>`` (на случай частично вырезанного токена).
_TOKEN_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{25,}")
_BOT_URL_RE = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")

#: Замена, которую видит человек (синхронизировано с kino/logging_util.py).
REDACTED = "bot***"


def sanitize(text: str, token: str | None = None) -> str:
    """Вычищает токен из строки.

    ``token`` — если известен конкретный токен, он заменяется на маску
    (``pro_code.crypto.mask``), даже если не попал под общий шаблон.
    Идемпотентна: уже вычищенный текст не меняется.
    """
    if not text:
        return text
    cleaned = text
    if token:
        cleaned = cleaned.replace(token, _mask_token(token))
    cleaned = _BOT_URL_RE.sub("/bot" + REDACTED, cleaned)
    cleaned = _TOKEN_RE.sub(REDACTED, cleaned)
    return cleaned


class SecretFilter(logging.Filter):
    """Фильтр логирования: чистит запись до форматирования.

    Ставится на handler (не на логгер): ``logger.exception`` собирает
    traceback отдельно, поэтому чистить надо текст сообщения и ``exc_info``.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.msg = sanitize(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: sanitize(str(v)) for k, v in record.args.items()
                }
            else:
                record.args = tuple(sanitize(str(a)) for a in record.args)
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = sanitize(str(record.exc_info[1]))
        return True
