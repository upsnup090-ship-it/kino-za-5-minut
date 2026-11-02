"""Диспетчер: запускает polling-потоки + serve-цикл автопостинга.

Каждый бот работает в отдельном потоке. Падение одного бота не роняет
остальных (изоляция сбоев).

Serve-цикл (вариант A) — один поток на всех клиентов с планом auto.
Каждую минуту проверяет расписание слотов и публикует через бота клиента.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
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
from pro_code.kino_auto_db import KinoAutoDb
from pro_code.kino_adapter import run_auto_post, LLM_COST_PER_RUN
from project_ruch.ruch_db import RuchDb

logger = logging.getLogger(__name__)

#: Задержка между циклами polling при ошибке (сек)
POLL_BACKOFF = 3.0

#: Интервал serve-цикла (сек)
SERVE_INTERVAL = 60


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


def serve_worker(cfg: Config, db: ClientsDb, auto_db: KinoAutoDb) -> None:
    """Serve-цикл (вариант A): проверяет слоты всех клиентов по расписанию.

    Каждую минуту проверяет:
      1. Активен ли клиент (статус, LLM-лимит, не на паузе)
      2. Наступил ли слот по расписанию (weekday + time)
      3. Есть ли готовый пост от Kino (pipeline.run)
      4. Если нет — пробует резерв
      5. Учитывает LLM-расход
    """
    logger.info("serve-цикл запущен (интервал %dс)", SERVE_INTERVAL)

    # Необходим импорт конфига Kino для каждого пользователя.
    # Пока используем дефолтный cfg Kino — заглушка.
    try:
        from kino.config import load_config as load_kino_cfg
        kino_cfg = load_kino_cfg()
    except Exception as exc:
        logger.error("не удалось загрузить конфиг Kino: %s", exc)
        kino_cfg = None

    while True:
        try:
            now = datetime.now(timezone.utc)
            now_weekday = now.weekday()      # 0=Mon…6=Sun
            now_time = now.strftime("%H:%M")

            for client in cfg.clients:
                if client.plan != "auto":
                    continue
                if not client.active:
                    continue
                if client.channel_chat_id is None:
                    continue

                user_id = client.user_id

                # Проверка LLM-лимита
                if not db.can_use_llm(user_id):
                    continue

                # Проверка паузы
                if auto_db.is_paused(user_id):
                    continue

                # Проверяем слоты
                slots = auto_db.list_slots(user_id, enabled_only=True)
                for slot in slots:
                    if slot["weekday"] != now_weekday:
                        continue
                    if slot["time"] != now_time:
                        continue

                    # Проверяем, не публиковали ли уже в этом слоте
                    slot_at = f"{now.strftime('%Y-%m-%d')} {slot['time']}"
                    if auto_db.slot_done(user_id, slot["id"], slot_at):
                        continue

                    # Запуск поста
                    logger.info("serve: user_id=%d, слот %s %s", user_id, slot["weekday"], slot["time"])

                    if kino_cfg is None:
                        logger.warning("serve: Kino не загружен, пробуем резерв user_id=%d", user_id)
                        # Пробуем резерв напрямую
                        from pro_code.kino_adapter import _try_reserve
                        _try_reserve(client.token, client.channel_chat_id, user_id, auto_db)
                        continue

                    try:
                        result = run_auto_post(
                            client_token=client.token,
                            channel_chat_id=client.channel_chat_id,
                            user_id=user_id,
                            kino_cfg=kino_cfg,
                            auto_db=auto_db,
                        )
                        if result.get("published"):
                            db.add_llm_spend(user_id, LLM_COST_PER_RUN)
                    except Exception as exc:
                        logger.exception("serve: ошибка публикации user_id=%d: %s", user_id, exc)

                # --- LLM-лимит — уведомление ---
                if not db.can_use_llm(user_id):
                    try:
                        send_message(
                            client.token, user_id,
                            "❌ **Превышен лимит LLM-расхода ($1).**\n"
                            "Автопостинг приостановлен. Обратись к мастеру для пополнения.",
                        )
                    except Exception:
                        pass

        except Exception as exc:
            logger.exception("serve-цикл: ошибка: %s", exc)

        time.sleep(SERVE_INTERVAL)


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

    # Стартуем serve-цикл (только если есть клиенты с планом auto)
    auto_db = KinoAutoDb()
    serve_thread = threading.Thread(
        target=serve_worker,
        args=(cfg, db, auto_db),
        daemon=True,
        name="serve-worker",
    )
    serve_thread.start()

    logger.info(
        "диспетчер запущен: %d ботов, serve-цикл %s",
        len(threads),
        "активен" if any(c.plan == "auto" and c.channel_chat_id for c in cfg.clients) else "неактивен (нет auto-клиентов с каналом)",
    )

    # Держим основной поток живым
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("диспетчер остановлен по Ctrl+C")
