"""Транспорт админки: доступ, опрос апдейтов, цикл.

Чистая логика живёт в `screens`/`commands`/`callbacks`; здесь — сеть.
Доступ только по `ADMIN_CHAT_ID`, проверка на каждом апдейте; чужой чат —
отказ и запись в лог.
"""

from __future__ import annotations

import logging
from datetime import datetime

from kino.config import Config

from kino.admin.callbacks import handle_callback
from kino.admin.commands import handle_command
from kino.admin.common import CONFLICT_BACKOFF_MAX, CONFLICT_BACKOFF_START

logger = logging.getLogger(__name__)

# --- доступ ------------------------------------------------------------------


def is_admin(cfg: Config, chat_id: int | str | None) -> bool:
    """Проверка доступа. Пустой ADMIN_CHAT_ID — админки нет ни для кого.

    Осознанно: если переменная не задана, «пускать всех» было бы дырой.
    """
    if chat_id is None or not cfg.admin_chat_id:
        return False
    return str(chat_id).strip() == str(cfg.admin_chat_id).strip()


# --- транспорт ---------------------------------------------------------------


def poll_once(cfg: Config, offset: int | None = None, now: datetime | None = None) -> tuple[int | None, int]:
    """Один цикл: забрать апдейты, ответить, вернуть (новый offset, сколько).

    Чужой чат получает отказ и запись в лог; в offset он всё равно попадает,
    иначе Telegram отдавал бы его снова и снова.
    """
    from kino import tg_api

    updates = tg_api.get_updates(cfg, offset)
    handled = 0
    for update in updates:
        offset = int(update.get("update_id", 0)) + 1
        try:
            if _dispatch(cfg, update, now):
                handled += 1
        except Exception as exc:  # noqa: BLE001 — один битый апдейт не роняет цикл
            logger.exception("админка: апдейт не обработан (%s)", exc)
    return offset, handled


def _dispatch(cfg: Config, update: dict, now: datetime | None) -> bool:
    """Обрабатывает один апдейт. False — обрабатывать было нечего.

    Возвращаемое значение нужно счётчику в логе: «обработано 5» должно
    означать пять реальных ответов, а не пять пустых апдейтов.
    """
    from kino import tg_api

    message = update.get("message")
    callback = update.get("callback_query")

    if callback:
        chat_id = (callback.get("message") or {}).get("chat", {}).get("id")
        user_id = (callback.get("from") or {}).get("id")
        callback_id = str(callback.get("id", ""))
        if not (is_admin(cfg, chat_id) or is_admin(cfg, user_id)):
            logger.warning("админка: чужое нажатие кнопки из чата %s", chat_id)
            tg_api.answer_callback(cfg, callback_id, "Нет доступа")
            return True
        tg_api.answer_callback(cfg, callback_id)
        reply = handle_callback(cfg, str(callback.get("data", "")), now)
        message_id = (callback.get("message") or {}).get("message_id")
        target = chat_id or cfg.admin_chat_id
        if message_id:
            tg_api.edit_message(cfg, target, int(message_id), reply.text, reply_markup=reply.markup())
        else:
            tg_api.send_message(cfg, target, reply.text, reply_markup=reply.markup())
        return True

    if message:
        chat_id = (message.get("chat") or {}).get("id")
        text = str(message.get("text", ""))
        if not is_admin(cfg, chat_id):
            logger.warning("админка: чужое сообщение из чата %s", chat_id)
            # Отвечаем в тот чат, откуда пришло; если id нет — в админский.
            # Пустой chat_id Telegram отвергнет, поэтому подставляем осмысленное.
            target = chat_id if chat_id is not None else cfg.admin_chat_id
            if target:
                tg_api.send_message(cfg, target, "Нет доступа")
            return True
        if chat_id is None:
            logger.debug("админка: сообщение без chat_id, пропуск")
            return False
        reply = handle_command(cfg, text, now)
        tg_api.send_message(cfg, chat_id, reply.text, reply_markup=reply.markup())
        return True

    # Ни сообщения, ни кнопки: служебный апдейт (например, edited_message).
    logger.debug("админка: апдейт без message/callback_query, пропуск")
    return False


def run(cfg: Config, *, once: bool = False, tick: float = 2.0) -> int:
    """Цикл админки. `once` — один опрос и выход (для отладки и тестов).

    Две разные реакции на сбой опроса:

    - **`409 Conflict`** — другой процесс уже читает апдейты этим токеном.
      Повтор каждые две секунды не поможет и только засоряет лог, поэтому
      первое сообщение пишется как ERROR с объяснением, дальше — молчание,
      и backoff растёт до `CONFLICT_BACKOFF_MAX`. Как только опрос снова
      пошёл — пишем, что конфликт ушёл.
    - **всё остальное** — обычный WARNING и короткая пауза: сеть моргнула,
      Telegram ответил 502, прокси лёг. Это проходит само.
    """
    import time

    from kino.tg_api import TgConflict

    if not cfg.admin_chat_id:
        logger.error("админка: ADMIN_CHAT_ID не задан — работать некому")
        return 1
    offset: int | None = None
    logger.info("админка запущена, чат %s", cfg.admin_chat_id)

    #: Растёт вдвое на каждом подряд идущем конфликте, сбрасывается успехом.
    conflict_backoff = CONFLICT_BACKOFF_START
    #: Сообщение печатается один раз на серию, а не на каждую попытку.
    conflict_reported = False

    while True:
        try:
            offset, handled = poll_once(cfg, offset)
            if handled:
                logger.info("админка: обработано апдейтов %d", handled)
            if conflict_reported:
                logger.info("админка: 409 ушёл, опрос снова работает")
                conflict_reported = False
            conflict_backoff = CONFLICT_BACKOFF_START
        except TgConflict as exc:
            if not conflict_reported:
                logger.error(
                    "админка: %s Ждём до %.0fс между попытками. "
                    "Проверь: `systemctl status kino-admin` и не запущен ли "
                    "второй экземпляр вручную.",
                    exc,
                    exc.retry_after,
                )
                conflict_reported = True
            else:
                logger.debug("админка: 409 всё ещё держится")
            if once:
                return 1
            time.sleep(min(conflict_backoff, CONFLICT_BACKOFF_MAX))
            conflict_backoff = min(conflict_backoff * 2, CONFLICT_BACKOFF_MAX)
            continue
        except Exception as exc:  # noqa: BLE001 — цикл 24/7 не должен умирать
            logger.warning("админка: опрос не удался (%s)", exc)
        if once:
            return 0
        time.sleep(tick)
