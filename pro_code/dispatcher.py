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
from pro_code import access, activation
from pro_code.crypto import mask as mask_token
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
from pro_code.kino_adapter import run_auto_post, _try_reserve, LLM_COST_PER_RUN
from project_ruch.ruch_db import RuchDb

logger = logging.getLogger(__name__)

#: Задержка между циклами polling при ошибке (сек)
POLL_BACKOFF = 3.0

#: Интервал serve-цикла (сек)
SERVE_INTERVAL = 60


def _deny(token: str, chat_id: int) -> None:
    """Ответить постороннему отказом. В БД при этом не пишем ничего."""
    try:
        send_message(token, chat_id, access.DENY_TEXT)
    except TgError:
        pass


def verify_bot(token: str) -> bool:
    """Проверяет токен через getMe. True — токен рабочий."""
    try:
        info = get_me(token)
        logger.info("бот %s (%s) — токен OK", info.get("username"), info.get("id"))
        return True
    except TgError as exc:
        logger.warning("токен неактивен (%s): %s", mask_token(token), exc)
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

    logger.info("polling запущен для user_id=%d (бот %s)", user_id, mask_token(token))

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
    """Обрабатывает один апдейт (сообщение или callback).

    Бот клиента публичен: апдейт придёт с любым ``from.id``. Владельцем
    считается только сам клиент — всё остальное отклоняется до любых
    записей в БД (иначе посторонний постил в его каналы от его имени).
    """
    message = update.get("message")
    callback = update.get("callback_query")

    # --- доступ: только владелец бота --------------------------------
    actor = access.actor_of(update)
    if not access.is_owner(actor, user_id):
        chat_id = None
        if callback is not None:
            chat_id = (callback.get("message") or {}).get("chat", {}).get("id")
            cb_id = str(callback.get("id", ""))
            if cb_id:
                try:
                    answer_callback(token, cb_id)
                except TgError:
                    pass
        elif message is not None:
            chat_id = (message.get("chat") or {}).get("id")
        logger.warning(
            "отказ: actor=%s пытался управлять ботом user_id=%d", actor, user_id
        )
        if chat_id:
            _deny(token, chat_id)
        return

    is_owner = (user_id == cfg.admin_chat_id)

    if callback:
        chat_id = (callback.get("message") or {}).get("chat", {}).get("id")
        cb_id = str(callback.get("id", ""))
        data = str(callback.get("data", ""))
        if chat_id:
            answer_callback(token, cb_id)
            # Кнопки ручного постинга: cb:<действие>
            if data.startswith("cb:") and ruch_db and db:
                action = data[3:]
                try:
                    handle_message(
                        token, user_id, plan, is_owner, chat_id, action,
                        clients_db=db, ruch_db=ruch_db,
                    )
                except Exception as exc:
                    logger.exception("callback %s не обработан: %s", data, exc)
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

        handle_message(token, user_id, plan, is_owner, chat_id, text, clients_db=db, ruch_db=ruch_db, raw_message=message)
        return


def master_worker(cfg: Config, db: ClientsDb, ruch_db: RuchDb) -> None:
    """Поток polling главного бота (мастера).

    Через него клиенты активируют своих ботов: /start → токен → getMe →
    сохранение → запуск. Всё остальное — обычное меню мастера.
    """
    token = cfg.master_token
    if not token:
        return
    offset: int | None = None
    logger.info("polling главного бота запущен")

    while True:
        try:
            updates = get_updates(token, offset=offset)
            for update in updates:
                offset = int(update.get("update_id", 0)) + 1
                _dispatch_master(cfg, db, ruch_db, token, update)
        except TgConflict:
            logger.warning("409 Conflict у главного бота — другой процесс?")
            time.sleep(10)
        except TgError as exc:
            logger.error("TgError у главного бота: %s", exc)
            time.sleep(POLL_BACKOFF)
        except Exception as exc:
            logger.exception("необработанная ошибка главного бота: %s", exc)
            time.sleep(POLL_BACKOFF)


def _dispatch_master(
    cfg: Config,
    db: ClientsDb,
    ruch_db: RuchDb,
    token: str,
    update: dict,
) -> None:
    """Обработать апдейт главного бота.

    Доступ: активация своего бота — всем (ради этого мастер-бот и создан),
    любые другие команды и кнопки — только ``admin_chat_id``.
    """
    message = update.get("message")
    callback = update.get("callback_query")
    actor = access.actor_of(update)

    if callback:
        chat_id = (callback.get("message") or {}).get("chat", {}).get("id")
        cb_id = str(callback.get("id", ""))
        data = str(callback.get("data", ""))
        if not chat_id:
            return
        answer_callback(token, cb_id)
        action = (
            data[len(activation_cb_prefix()):]
            if data.startswith(activation_cb_prefix())
            else None
        )
        if action in ACTIVATION_CALLBACKS:
            # Кнопки активации: клиент ведёт СВОЕГО бота. user_id — из from.id.
            # Сюда попадают только кнопки активации, а не любой ``cb:``:
            # у ручного постинга тот же префикс, и без этой отсечки чужая
            # кнопка уходила бы в активацию мимо админ-гейта.
            target_user = actor if actor is not None else chat_id
            try:
                activation.handle(token, chat_id, target_user, action, db)
            except Exception as exc:
                logger.exception("callback главного бота %s: %s", data, exc)
        elif not access.is_admin(actor, cfg.admin_chat_id):
            logger.warning(
                "отказ: actor=%s нажал кнопку мастер-бота (%s)", actor, data
            )
            _deny(token, chat_id)
        return

    if not message:
        return

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return

    text = str(message.get("text", ""))
    user_id = (message.get("from") or {}).get("id", chat_id)
    actor = access.actor_of(update)

    try:
        # Активация поглощает сообщение, если клиент в этом шаге.
        # Её открываем всем: мастер-бот для того и нужен, чтобы клиент
        # активировал СВОЕГО бота (user_id берётся из from.id, не из чата).
        if activation.handle(token, chat_id, user_id, text, db):
            return
        # Всё остальное на мастер-боте — только мастер.
        if not access.is_admin(actor, cfg.admin_chat_id):
            logger.warning(
                "отказ: actor=%s пытался дать команду мастер-боту", actor
            )
            _deny(token, chat_id)
            return
        handle_message(
            token, user_id, "free", True, chat_id, text,
            clients_db=db, ruch_db=ruch_db, raw_message=message,
        )
    except Exception as exc:
        logger.exception("ошибка обработки апдейта главного бота: %s", exc)


#: Кнопки активации на мастер-боте. Префикс ``cb:`` общий с ручным
#: постингом, поэтому маршрут в активацию определяется по конкретному
#: действию, а не только по префиксу (иначе любая ``cb:``-кнопка обходила
#: бы админ-гейт мастер-бота).
ACTIVATION_CALLBACKS = frozenset({
    "/start",
    "/my_bots",
    "/replace_token",
    "/cancel",
})


def activation_cb_prefix() -> str:
    """Префикс callback_data кнопок активации (общий для кнопок бота)."""
    return "cb:"


def serve_worker(cfg: Config, db: ClientsDb, auto_db: KinoAutoDb) -> None:
    """Serve-цикл (вариант A): слоты всех клиентов, публикация их ботами.

    Каждую минуту: активен ли клиент (статус, LLM-лимит, не на паузе),
    наступил ли слот по расписанию, есть ли каналы автопостинга.

    Время сверяется в зоне клиента (``RuchConfig().timezone``), а не в UTC:
    расписание задано в зоне клиента, и UTC-сравнение сдвигало слот (для
    Москвы — на 3 часа). Тот же дефект был в ``repeating.py``, здесь он был
    воспроизведён заново.

    На один слот — **один прогон Kino на клиента** (генерация стоит денег),
    публикация сразу во все его каналы автопостинга. Раньше цикл делал
    отдельный полный прогон LLM на каждый канал.

    Журнал прогонов: ``start_run`` перед работой, ``finish_run`` после —
    иначе ``slot_done`` не видел слот и он повторялся.
    """
    logger.info("serve-цикл запущен (интервал %dс)", SERVE_INTERVAL)

    from project_ruch.config import RuchConfig

    try:
        from kino.config import load_config as load_kino_cfg
        kino_cfg = load_kino_cfg()
    except Exception as exc:
        logger.error("не удалось загрузить конфиг Kino: %s", exc)
        kino_cfg = None

    #: Зона клиента — в ней задано расписание.
    tz = RuchConfig().timezone
    #: Кого уже предупредили о лимите: уведомление раз, а не каждую минуту.
    notified: set[int] = set()

    while True:
        try:
            now = datetime.now(tz)
            now_weekday = now.weekday()      # 0=Mon…6=Sun
            now_time = now.strftime("%H:%M")

            for client in cfg.clients:
                if client.plan != "auto" or not client.active:
                    continue

                user_id = client.user_id

                # Пауза — раньше лимита: на паузе расход не идёт.
                if auto_db.is_paused(user_id):
                    continue

                # Лимит LLM: уведомляем один раз, затем молчим.
                if not db.can_use_llm(user_id):
                    if user_id not in notified:
                        notified.add(user_id)
                        try:
                            send_message(
                                client.token, user_id,
                                "❌ **Превышен лимит LLM-расхода ($1).**\n"
                                "Автопостинг приостановлен. Обратись к мастеру для пополнения.",
                            )
                        except Exception:
                            pass
                    continue
                notified.discard(user_id)

                # Каналы автопостинга — все с purpose auto/both.
                auto_channels = db.list_channels(user_id, purpose="auto")
                if not auto_channels:
                    continue
                channels = [ch["chat_id"] for ch in auto_channels]

                for slot in auto_db.list_slots(user_id, enabled_only=True):
                    if slot["weekday"] != now_weekday or slot["time"] != now_time:
                        continue

                    slot_at = f"{now.strftime('%Y-%m-%d')} {slot['time']}"
                    if auto_db.slot_done(user_id, slot["id"], slot_at):
                        continue

                    logger.info(
                        "serve: user_id=%d, слот %s %s, каналов: %d",
                        user_id, slot["weekday"], slot["time"], len(channels),
                    )
                    run_id = auto_db.start_run(user_id, slot["id"], slot_at)

                    if kino_cfg is None:
                        logger.warning(
                            "serve: Kino не загружен, пробуем резерв user_id=%d",
                            user_id,
                        )
                        result = _try_reserve(None, client.token, channels, user_id, auto_db)
                    else:
                        result = run_auto_post(
                            client_token=client.token,
                            channels=channels,
                            user_id=user_id,
                            kino_cfg=kino_cfg,
                            auto_db=auto_db,
                            run_id=run_id,
                        )

                    if result.get("published"):
                        db.add_llm_spend(user_id, LLM_COST_PER_RUN)
                        auto_db.finish_run(
                            run_id, outcome="published",
                            link=result.get("link") or None,
                            format=result.get("format") or None,
                            kind=result.get("kind") or None,
                            llm_cost=LLM_COST_PER_RUN,
                        )
                    else:
                        auto_db.finish_run(
                            run_id, outcome="failed",
                            reason=str(result.get("error", "")),
                        )

        except Exception as exc:
            logger.exception("serve-цикл: ошибка: %s", exc)

        time.sleep(SERVE_INTERVAL)


def run_dispatcher(cfg: Config) -> None:
    """Запускает диспетчер: главный бот, клиентские боты, serve-цикл."""
    # Объекты БД создаются здесь, но соединение НЕ открывается: оно ленивое
    # и живёт в потоке, который первым к нему обратился (pro_code/db.py).
    # Раньше соединение открывалось в главном потоке, и каждый бот в своём
    # потоке получал ProgrammingError — бот не отвечал вообще.
    db = ClientsDb()
    ruch_db = RuchDb()

    # Реестр живых потоков: user_id → Thread. Нужен, чтобы поднять бота
    # после активации без рестарта и не поднять его дважды.
    registry: dict[int, threading.Thread] = {}
    lock = threading.Lock()

    def spawn_bot(user_id: int, token: str) -> None:
        """Поднять polling бота клиента. Повторный вызов — no-op."""
        with lock:
            existing = registry.get(user_id)
            if existing is not None and existing.is_alive():
                logger.info("бот user_id=%d уже запущен, повтор не нужен", user_id)
                return
            client = ClientBot(
                user_id=user_id, token=token, plan="free", active=True, source="db"
            )
            row = db.get_client(user_id)
            if row and row.get("plan") in ("free", "auto"):
                client.plan = row["plan"]
            t = threading.Thread(
                target=poll_worker,
                args=(cfg, client, db, ruch_db),
                daemon=True,
                name=f"bot-{user_id}",
            )
            t.start()
            registry[user_id] = t
            logger.info("бот user_id=%d запущен (поток %s)", user_id, t.name)

    # Активация через главного бота поднимает бота этим колбэком.
    activation.set_spawner(spawn_bot)

    # --- клиентские боты из конфига (.env + БД) ---
    for client in cfg.clients:
        client.active = verify_bot(client.token)
        try:
            db.upsert_client(
                client.user_id, plan=client.plan,
                status="active" if client.active else "inactive",
            )
        except Exception as exc:
            logger.error("не удалось записать клиента %d: %s", client.user_id, exc)

        if not client.active:
            logger.warning("бот user_id=%d пропущен (неактивный токен)", client.user_id)
            continue

        spawn_bot(client.user_id, client.token)

    # --- главный бот (мастер) ---
    master_thread = None
    if cfg.master_token:
        if verify_bot(cfg.master_token):
            master_thread = threading.Thread(
                target=master_worker,
                args=(cfg, db, ruch_db),
                daemon=True,
                name="master-bot",
            )
            master_thread.start()
            logger.info("главный бот запущен")
        else:
            logger.error("MASTER_BOT_TOKEN неактивен — активация клиентов недоступна")
    else:
        logger.warning("MASTER_BOT_TOKEN не задан — главный бот не поднят")

    # --- serve-цикл автопостинга ---
    auto_db = KinoAutoDb()
    serve_thread = threading.Thread(
        target=serve_worker,
        args=(cfg, db, auto_db),
        daemon=True,
        name="serve-worker",
    )
    serve_thread.start()

    logger.info(
        "диспетчер запущен: %d ботов, главный %s, serve-цикл %s",
        len(registry),
        "активен" if master_thread else "нет",
        "активен" if any(c.plan == "auto" for c in cfg.clients) else "неактивен",
    )

    # Держим основной поток живым
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("диспетчер остановлен по Ctrl+C")
    finally:
        # Закрыть соединения всех потоков: иначе остаются висеть WAL-файлы,
        # а незакоммиченные транзакции теряются.
        serve_thread.join(timeout=5)
        db.close_all()
        ruch_db.close_all()
        auto_db.close_all()
