"""Шифрование токенов клиентских ботов.

Токен бота — это полный доступ к боту: кто его знает, тот им управляет.
В БД он лежит зашифрованным (``bot_tokens.token_enc``), ключ — в ``.env``
(``TOKEN_ENCRYPTION_KEY``), отдельно от БД. Копия одной БД без ключа
токенов не даёт.

Алгоритм — Fernet из ``cryptography``: AES-128-CBC + HMAC-SHA256,
случайный IV на каждое сообщение, аутентификация встроена. Не самопал.

Ключ генерируется один раз:

    python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Потеря ключа = потеря токенов. Клиенты вставляют заново. Это правильное
поведение: иначе шифрование ничего не стоит.
"""

from __future__ import annotations

import logging
import os
from typing import Final

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

#: Имя переменной окружения с ключом.
ENV_KEY: Final = "TOKEN_ENCRYPTION_KEY"

#: Префикс зашифрованного значения в БД. Помогает отличить шифротекст от
#: случайно записанного открытого токена при миграции.
PREFIX: Final = "enc:"


class CryptoError(Exception):
    """Ошибка шифрования: нет ключа, битый ключ, не расшифровывается."""


def generate_key() -> str:
    """Сгенерировать новый ключ Fernet (строка для .env)."""
    return Fernet.generate_key().decode()


class TokenCipher:
    """Шифрует и расшифровывает токены ботов.

    Ключ берётся из аргумента или из ``TOKEN_ENCRYPTION_KEY``. Без ключа
    объект не создаётся — молча писать открытым текстом хуже, чем упасть
    на старте с понятным сообщением.
    """

    def __init__(self, key: str | bytes | None = None) -> None:
        raw = key if key is not None else os.environ.get(ENV_KEY, "")
        if isinstance(raw, bytes):
            raw = raw.decode()
        raw = (raw or "").strip()
        if not raw:
            raise CryptoError(
                f"{ENV_KEY} не задан. Сгенерируй ключ:\n"
                f'  python3 -c "from cryptography.fernet import Fernet; '
                f'print(Fernet.generate_key().decode())"\n'
                f"и впиши в .env как {ENV_KEY}=<ключ>"
            )
        try:
            self._fernet = Fernet(raw.encode() if isinstance(raw, str) else raw)
        except (ValueError, TypeError) as exc:
            raise CryptoError(
                f"{ENV_KEY} не похож на ключ Fernet (нужно 44 символа base64): {exc}"
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        """Зашифровать строку. Возвращает ``enc:<base64>``."""
        if not plaintext:
            raise CryptoError("пустая строка не шифруется")
        token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return PREFIX + token

    def decrypt(self, stored: str) -> str:
        """Расшифровать значение из БД.

        Принимает и ``enc:<base64>``, и голый base64 (на случай ручной
        правки). Открытый токен (без префикса и не base64-Fernet) вернёт
        как есть — так читаются записи, сделанные до шифрования.
        """
        if not stored:
            raise CryptoError("пустое значение не расшифровывается")
        payload = stored[len(PREFIX):] if stored.startswith(PREFIX) else stored
        try:
            return self._fernet.decrypt(payload.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
            if not stored.startswith(PREFIX):
                # Не наш шифротекст — вероятно, открытый токен из старой записи.
                logger.warning("значение без префикса %s не расшифровалось, "
                               "возвращаю как есть", PREFIX)
                return stored
            raise CryptoError(
                "не расшифровывается: ключ не тот или запись повреждена"
            ) from exc

    def is_encrypted(self, stored: str) -> bool:
        """Похоже ли значение на наш шифротекст."""
        return bool(stored) and stored.startswith(PREFIX)


def mask(token: str, keep: int = 8) -> str:
    """Маска для логов: ``12345678…``. Токен целиком не логируем никогда."""
    if not token:
        return ""
    if len(token) <= keep:
        return "…"
    return token[:keep] + "…"
