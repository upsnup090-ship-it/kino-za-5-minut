"""Тонкий клиент Bot API для админки: getUpdates, sendMessage, editMessage.

Отдельно от `kino/publish.py` намеренно. Публикация — про канал: подпись,
картинка, вариация, фолбэки. Админка — про диалог: инлайн-клавиатура,
редактирование сообщения вместо отправки нового, ответ на callback. Общая у
них только транспортная база (URL, прокси, повторы при 429), и та вынесена
отсюда в маленькие функции, а не в общий класс.

Публикация не тронута: её `_send_with_retry` возвращает bool и не знает про
`reply_markup`. Здесь нужен разбор ответа — без него нельзя получить
`message_id` и `callback_query_id`.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests
from requests.exceptions import HTTPError, RequestException

from kino.config import Config
from kino.net_guard import post as http_post
from kino.publish import API_BASE, _proxies, _retry_after

logger = logging.getLogger(__name__)

#: Сколько ждать длинный опрос getUpdates (секунды). Telegram держит
#: соединение открытым, если пришёл параметр timeout.
LONG_POLL_TIMEOUT = 25


class TgError(Exception):
    """Ошибка Bot API: сеть, HTTP или `ok: false` в теле.

    Текст ошибки уходит в лог и в чат админа, поэтому токен из него вычищается
    на входе: `requests` вставляет полный URL в сообщение исключения, и
    `str(exc)` у `ConnectionError` содержал бы `bot<TOKEN>`.
    """

    def __init__(self, message: str) -> None:
        from kino.logging_util import sanitize

        super().__init__(sanitize(message))


class TgConflict(TgError):
    """`409 Conflict` — другой процесс уже читает `getUpdates` этим токеном.

    Отдельный тип, потому что реакция другая: повторять бессмысленно, пока
    второй процесс жив. Админка обязана сказать это внятно и уйти в
    длинный backoff, а не долбить Telegram каждые две секунды.

    Практический источник: два экземпляра `kino admin` (systemd + ручной
    запуск), или админка и старый бот на одном `BOT_TOKEN`.
    """

    def __init__(self, message: str, *, retry_after: float = 60.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _url(cfg: Config, method: str) -> str:
    if not cfg.bot_token:
        raise TgError("BOT_TOKEN не задан: админка без него не работает")
    return f"{API_BASE}/bot{cfg.bot_token}/{method}"


def call(
    cfg: Config,
    method: str,
    payload: dict[str, Any] | None = None,
    *,
    attempts: int | None = None,
) -> dict[str, Any]:
    """Вызов метода Bot API. Возвращает поле `result` из ответа.

    Повторы: 429 с `retry_after` и сетевые ошибки. Прочие HTTP-ошибки не
    повторяются — 400 (битый payload) и 403 (бот заблокирован) сами не
    пройдут, а циклический повтор только засоряет лог.
    """
    max_attempts = attempts if attempts is not None else max(1, cfg.settings.retry_max_attempts)
    base_delay = cfg.settings.retry_backoff
    data = dict(payload or {})
    # requests умеет кодировать вложенные структуры только как строки.
    for key in ("reply_markup",):
        if key in data and not isinstance(data[key], str):
            data[key] = json.dumps(data[key], ensure_ascii=False)

    last_error = ""
    for attempt in range(max_attempts):
        try:
            response = http_post(
                _url(cfg, method),
                data=data,
                timeout=cfg.settings.telegram_timeout,
                proxies=_proxies(cfg),
                net_class="publish",
            )
        except RequestException as exc:
            last_error = exc.__class__.__name__
            wait = base_delay * (attempt + 1)
            logger.warning("%s: %s, ждём %.0fс (попытка %d/%d)",
                           method, last_error, wait, attempt + 1, max_attempts)
            if attempt < max_attempts - 1:
                time.sleep(wait)
            continue

        if response.status_code == 429:
            wait = _retry_after(response, base_delay) + (2 ** attempt)
            last_error = "429"
            logger.warning("%s: 429, ждём %.0fс (попытка %d/%d)",
                           method, wait, attempt + 1, max_attempts)
            if attempt < max_attempts - 1:
                time.sleep(wait)
            continue

        if response.status_code == 409:
            # Другой процесс читает getUpdates этим токеном. Повтор не
            # поможет, пока он жив: это не сетевая ошибка, а конфликт права
            # на апдейты. Отдаём отдельный тип — цикл админки уйдёт в backoff
            # и напишет человеку, что именно случилось.
            description = ""
            try:
                description = str(response.json().get("description", ""))
            except ValueError:
                description = response.text[:200]
            raise TgConflict(
                f"{method}: HTTP 409 — {description or 'Conflict'}. "
                "Похоже, другой процесс (systemd или ручной запуск) уже "
                "читает апдейты этим токеном."
            )

        if response.status_code >= 400:
            raise TgError(f"{method}: HTTP {response.status_code} {response.text[:200]}")

        try:
            body = response.json()
        except ValueError as exc:
            raise TgError(f"{method}: ответ не JSON") from exc
        if not body.get("ok"):
            raise TgError(f"{method}: ok=false, {body.get('description', '')}")
        return body.get("result") or {}

    raise TgError(f"{method}: не доставлено за {max_attempts} попыток ({last_error})")


def get_updates(cfg: Config, offset: int | None = None, *, long_poll: int = LONG_POLL_TIMEOUT) -> list[dict]:
    """Длинный опрос обновлений. Пустой список — новых нет."""
    payload: dict[str, Any] = {
        "timeout": long_poll,
        # Только то, что умеет админка: команды и нажатия кнопок.
        "allowed_updates": json.dumps(["message", "callback_query"]),
    }
    if offset is not None:
        payload["offset"] = offset
    result = call(cfg, "getUpdates", payload, attempts=2)
    return result if isinstance(result, list) else []


def send_message(
    cfg: Config,
    chat_id: str | int,
    text: str,
    *,
    reply_markup: dict | None = None,
    parse_mode: str | None = None,
) -> int:
    """Отправляет сообщение. Возвращает message_id."""
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    result = call(cfg, "sendMessage", payload)
    return int(result.get("message_id", 0))


def edit_message(
    cfg: Config,
    chat_id: str | int,
    message_id: int,
    text: str,
    *,
    reply_markup: dict | None = None,
) -> bool:
    """Правит сообщение. False — текст не изменился (Telegram отвечает 400).

    Это штатный случай: пользователь нажал ту же кнопку дважды. Ошибку
    глотаем осознанно, иначе двойной тап выглядит как сбой.
    """
    payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        call(cfg, "editMessageText", payload, attempts=2)
        return True
    except TgError as exc:
        if "message is not modified" in str(exc):
            return False
        raise


def answer_callback(cfg: Config, callback_id: str, text: str = "") -> None:
    """Гасит «часики» на кнопке. Без этого Telegram крутит их минуту."""
    payload: dict[str, Any] = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    try:
        call(cfg, "answerCallbackQuery", payload, attempts=1)
    except TgError as exc:
        logger.debug("answerCallbackQuery: %s", exc)


def delete_message(cfg: Config, chat_id: str | int, message_id: int) -> bool:
    try:
        call(cfg, "deleteMessage", {"chat_id": chat_id, "message_id": message_id}, attempts=1)
        return True
    except TgError as exc:
        logger.debug("deleteMessage: %s", exc)
        return False
