"""Адаптер для вызова пайплайна Kino с per-client токеном и каналом.

Kino (project/kino/pipeline.py) не зависит от токена/канала — он генерирует
контент. Этот адаптер:
1. Запускает pipeline.run() (глобальный cfg Kino)
2. Берёт готовый пост и публикует через бота клиента (его токен, его канал)
3. Учитывает LLM-расход
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pro_code.tg_api import send_message as tg_send_message
from pro_code.kino_auto_db import KinoAutoDb

logger = logging.getLogger(__name__)

#: Условная стоимость одного LLM-прогона (будет уточнена)
LLM_COST_PER_RUN = 0.001


def run_auto_post(
    client_token: str,
    channel_chat_id: int,
    user_id: int,
    kino_cfg: Any,  # Config из project/kino/config.py
    auto_db: KinoAutoDb,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Запустить один цикл автопостинга для клиента.

    Возвращает dict с результатом.
    """
    try:
        from kino.pipeline import run as kino_run
    except ImportError as exc:
        logger.error("Kino не доступен: %s", exc)
        return {"ok": False, "error": f"Kino недоступен: {exc}"}

    # Запуск пайплайна (глобальный конфиг Kino)
    try:
        result = kino_run(kino_cfg, dry_run=dry_run)
    except Exception as exc:
        logger.exception("ошибка пайплайна для user_id=%d: %s", user_id, exc)
        return {"ok": False, "error": str(exc)}

    if dry_run:
        return {"ok": True, "dry_run": True, "format": result.format}

    # Если пост готов — публикуем через бота клиента
    if result.published:
        return _publish_via_client(
            client_token, channel_chat_id, user_id, result, auto_db,
        )

    # Если судья отбил или другая ошибка — пробуем резерв
    if result.verdict == "reject" or not result.published:
        return _try_reserve(
            client_token, channel_chat_id, user_id, auto_db,
        )

    return {"ok": False, "error": "пост не опубликован"}


def _publish_via_client(
    token: str,
    chat_id: int,
    user_id: int,
    result: Any,
    auto_db: KinoAutoDb,
) -> dict[str, Any]:
    """Публикует готовый пост через бота клиента."""
    from kino.publish import publish as kino_publish

    try:
        success = kino_publish(
            None,  # cfg — не нужен, передаём token и chat_id напрямую
            caption=getattr(result, "caption", ""),
            image_url=getattr(result, "image_url", ""),
            dry_run=False,
            kind=getattr(result, "kind", "text"),
            link_url=getattr(result, "link", ""),
            extra_images=getattr(result, "extra_images", None),
            poll_question=getattr(result, "poll_question", ""),
            poll_options=getattr(result, "poll_options", None),
            seen_hashes=None,
            bot_token=token,
            channel_chat_id=str(chat_id),
        )
    except Exception as exc:
        logger.exception("публикация через клиента не удалась user_id=%d: %s", user_id, exc)
        # Пробуем резерв
        return _try_reserve(token, chat_id, user_id, auto_db)

    if success:
        # Учитываем расход
        auto_db.finish_run(
            0, outcome="published", format=getattr(result, "format", ""),
            kind=getattr(result, "kind", ""), llm_cost=LLM_COST_PER_RUN,
        )
        return {"ok": True, "published": True, "format": getattr(result, "format", "")}
    else:
        return _try_reserve(token, chat_id, user_id, auto_db)


def _try_reserve(
    token: str,
    chat_id: int,
    user_id: int,
    auto_db: KinoAutoDb,
) -> dict[str, Any]:
    """Попробовать опубликовать пост из резерва клиента."""
    item = auto_db.take_reserve(user_id)
    if not item:
        return {"ok": False, "error": "резерв пуст"}

    from kino.publish import publish as kino_publish

    try:
        success = kino_publish(
            None,
            caption=item.get("caption", ""),
            image_url=item.get("image_url", ""),
            dry_run=False,
            kind=item.get("kind", "text"),
            link_url=item.get("link", ""),
            bot_token=token,
            channel_chat_id=str(chat_id),
        )
    except Exception as exc:
        logger.error("публикация из резерва не удалась user_id=%d: %s", user_id, exc)
        return {"ok": False, "error": str(exc)}

    if success:
        return {"ok": True, "published": True, "from_reserve": True}
    return {"ok": False, "error": "публикация из резерва не удалась"}
