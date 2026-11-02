"""Диспетчер: запускает polling-потоки для каждого бота клиента.

Каждый бот работает в отдельном потоке. Падение одного бота не роняет
остальных (изоляция сбоев).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from pro_code.config import Config, ClientBot
from pro_code.clients_db import ClientsDb
from pro_code.tg_api import (
    TgConflict,
    TgError,
    answer_callback,
    edit_message,
    get_me,
    get_updates,
    send_message,
)
from pro_code.router import build_main_menu, handle_message, handle_no_token
from project_ruch.ruch_db import RuchDb

logger = logging.getLogger(__name__)

#: Задержка между циклами polling при ошибке (сек)
POLL_BACKOFF = 3.0


def verify_bot(token: str) -> bool:
    """Проверяет токен через getMe. True — токен рабочий."""
    try:
        info = get_me(token)
        logger.info("бот %s (%s) — токен OK", info.get("username"), info.get("id"))
        return True
    except TgError as exc:
        logger.warning("токен неактивен (%s): %s", token[:12], exc)
        return False


def poll_worker(
    cfg: Config,
    client: ClientBot,
    db: ClientsDb,
    ruch_db: RuchDb,
) -> None:
    """Поток polling для одного бота клиента."""
    token = client.token
    user_id = client.user_id
    offset: int | None = None

    # Привязка клиента в БД (если нет)
    try:
        existing = db.get_client(user_id)
        if not existing:
            db.upsert_client(user_id, plan=client.plan, status="active" if client.active else "inactive")
    except Exception as exc:
        logger.error("БД клиентов недоступна для user_id=%d: %s", user_id, exc)

    if not client.active:
        logger.info("бот user_id=%d неактивен, polling не запущен", user_id)
        return

    logger.info("polling запущен для user_id=%d (бот %s…)", user_id, token[:12])

    while True:
        try:
            updates = get_updates(token, offset=offset)
            for update in updates:
                offset = int(update.get("update_id", 0)) + 1
                _dispatch_update(cfg, db, ruch_db, token, user_id, client.plan, update)
        except TgConflict:
            logger.warning("409 Conflict для user_id=%d — другой процесс?", user_id)
            time.sleep(10)
        except TgError as exc:
            logger.error("TgError для user_id=%d: %s", user_id, exc)
            time.sleep(POLL_BACKOFF)
        except Exception as exc:
            logger.exception("необработанная ошибка в polling user_id=%d: %s", user_id, exc)
            time.sleep(POLL_BACKOFF)


def _dispatch_update(
    cfg: Config,
    db: ClientsDb,
    ruch_db: RuchDb,
    token: str,
    user_id: int,
    plan: str,
    update: dict,
) -> None:
    """Обрабатывает один апдейт (сообщение или callback)."""
    message = update.get("message")
    callback = update.get("callback_query")

    is_owner = (user_id == cfg.admin_chat_id)

    if callback:
        chat_id = (callback.get("message") or {}).get("chat", {}).get("id")
        cb_id = str(callback.get("id", ""))
        data = str(callback.get("data", ""))
        if chat_id:
            answer_callback(token, cb_id)
            # TODO: обработка кнопок
        return

    if message:
        chat_id = (message.get("chat") or {}).get("id")
        text = str(message.get("text", ""))
        if not chat_id:
            return

        # Привязка чата (запоминаем, где бот присутствует)
        chat_type = (message.get("chat") or {}).get("type", "private")
        type_map = {
            "private": "private",
            "group": "chat",
            "supergroup": "chat",
            "channel": "channel",
        }
        try:
            db.add_binding(user_id, chat_id, type_map.get(chat_type, "chat"))
            ruch_db.save_binding(user_id, chat_id, type_map.get(chat_type, "chat"))
        except Exception:
            pass

        handle_message(token, user_id, plan, is_owner, chat_id, text, clients_db=db, ruch_db=ruch_db)
        return


def run_dispatcher(cfg: Config) -> None:
    """Запускает диспетчер: проверяет токены и стартует потоки."""
    db = ClientsDb()
    ruch_db = RuchDb()

    threads: list[threading.Thread] = []
    for client in cfg.clients:
        # Проверка токена
        client.active = verify_bot(client.token)
        # Обновляем статус в БД
        try:
            db.upsert_client(client.user_id, plan=client.plan, status="active" if client.active else "inactive")
        except Exception as exc:
            logger.error("не удалось записать клиента %d: %s", client.user_id, exc)

        if not client.active:
            logger.warning("бот user_id=%d пропущен (неактивный токен)", client.user_id)
            continue

        t = threading.Thread(
            target=poll_worker,
            args=(cfg, client, db, ruch_db),
            daemon=True,
            name=f"bot-{client.user_id}",
        )
        t.start()
        threads.append(t)

    logger.info("диспетчер запущен: %d ботов в работе", len(threads))

    # Держим основной поток живым
    try:
        while True:
            time.sleep(60)
            # TODO: serve-цикл (вариант A) — будет добавлен позже
    except KeyboardInterrupt:
        logger.info("диспетчер остановлен по Ctrl+C")
