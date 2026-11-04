"""Адаптер Kino для диспетчера: генерация мастер-конфигом, публикация — клиенту.

Kino (``project/kino``) генерирует контент по своему конфигу и умеет
публиковать сам — в канал мастера. Диспетчеру нужно другое: контент берём
у Kino, а публикуем **ботом клиента в каналы клиента**. Для этого
``kino.pipeline.run`` принимает ``publisher`` — функцию с сигнатурой
``kino.publish.publish``. Без неё поведение Kino прежнее (CLI, serve).

Что было сломано (аудит, P0-4):

1. ``import kino`` из точки входа падал — лечится ``pro_code/bootstrap``.
2. ``run()`` вызывался с ``dry_run`` по умолчанию, а публикация шла через
   глобальный ``cfg`` — пост уходил **в канал мастера**, после чего адаптер
   пытался опубликовать ещё раз клиенту.
3. Второй путь публикации передавал в ``kino.publish.publish`` ``cfg=None``
   → ``AttributeError: 'NoneType' object has no attribute 'settings'``.
4. ``finish_run(0, ...)`` не находил строку в ``runs`` → журнал прогонов пуст.

Теперь: один прогон Kino на клиента, публикация клиентским ``publisher``
во все его каналы, дедуп ссылок — per-client (``kino_auto_db.used_links``).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from pro_code.kino_auto_db import KinoAutoDb

logger = logging.getLogger(__name__)

#: Условная стоимость одного LLM-прогона (плейсхолдер, см. TODO ниже).
#: Реальный расход считается от входных/выходных токенов модели; сейчас
#: каждая генерация учитывается как 0.001 → лимит $1 ≈ 1000 прогонов.
LLM_COST_PER_RUN = 0.001
# TODO(фаза LLM-учёта): считать реальную стоимость по usage-ответу провайдера
# (prompt_tokens/completion_tokens × цены из settings.yaml) и писать её в runs.llm_cost.


def make_client_publisher(
    token: str,
    channels: list[int],
    user_id: int,
    auto_db: KinoAutoDb,
) -> Callable[..., bool]:
    """Собрать ``publisher`` для ``pipeline.run``: публикация клиенту.

    Возвращаемая функция повторяет сигнатуру ``kino.publish.publish`` —
    pipeline вызывает её как ``publish_fn(cfg, caption, image, dry_run, ...)``
    — но подставляет токен и каналы клиента. ``cfg`` (конфиг Kino) нужен
    публикации для ``settings`` (parse_mode, таймауты) — раньше сюда
    приходил ``None``, отсюда и падение.

    Дедуп per-client: ссылку, уже ушедшую этому клиенту, повторно не
    публикуем. Отметка ставится только после успешной отправки.
    """

    from kino.publish import publish as kino_publish

    def publisher(
        cfg: Any,
        caption: str,
        image_url: str,
        dry_run: bool,
        kind: str = "photo",
        link_url: str = "",
        extra_images: list[str] | None = None,
        poll_question: str = "",
        poll_options: list[str] | None = None,
        seen_hashes: set[str] | None = None,
        **_ignored: Any,
    ) -> bool:
        if link_url and auto_db.has_link(user_id, link_url):
            logger.info(
                "клиент user_id=%d: ссылка уже публиковалась, пропуск (%s)",
                user_id, link_url,
            )
            return False

        published_any = False
        for ch in channels:
            try:
                ok = kino_publish(
                    cfg,
                    caption,
                    image_url,
                    dry_run=False,
                    kind=kind,
                    link_url=link_url,
                    extra_images=extra_images,
                    poll_question=poll_question,
                    poll_options=poll_options,
                    seen_hashes=seen_hashes,
                    bot_token=token,
                    channel_chat_id=str(ch),
                )
            except Exception as exc:  # noqa: BLE001 — один канал не роняет остальные
                logger.exception(
                    "публикация user_id=%d в канал %s не удалась: %s",
                    user_id, ch, exc,
                )
                continue
            published_any = published_any or bool(ok)

        if published_any and link_url:
            auto_db.mark_link(user_id, link_url)
        return published_any

    return publisher


def run_auto_post(
    client_token: str,
    channels: list[int],
    user_id: int,
    kino_cfg: Any,
    auto_db: KinoAutoDb,
    *,
    dry_run: bool = False,
    run_id: int | None = None,
) -> dict[str, Any]:
    """Один цикл автопостинга для клиента: генерация + публикация в его каналы.

    Возвращает ``{ok, published, format, ...}``.
    """
    try:
        from kino.pipeline import run as kino_run
    except ImportError as exc:
        logger.error("Kino недоступен: %s", exc)
        return {"ok": False, "error": f"Kino недоступен: {exc}"}

    publisher = make_client_publisher(client_token, channels, user_id, auto_db)

    try:
        result = kino_run(kino_cfg, dry_run=dry_run, publisher=publisher)
    except Exception as exc:
        logger.exception("ошибка пайплайна для user_id=%d: %s", user_id, exc)
        return {"ok": False, "error": str(exc)}

    if dry_run:
        return {"ok": True, "dry_run": True, "format": getattr(result, "format", "")}

    published = bool(getattr(result, "published", False))
    if published:
        return {
            "ok": True,
            "published": True,
            "format": getattr(result, "format", ""),
            "kind": getattr(result, "kind", ""),
            "link": getattr(result, "link", ""),
        }

    # Пост не вышел — пробуем резерв клиента (per-client, kino_auto_db).
    return _try_reserve(kino_cfg, client_token, channels, user_id, auto_db)


def _try_reserve(
    kino_cfg: Any,
    token: str,
    channels: list[int],
    user_id: int,
    auto_db: KinoAutoDb,
) -> dict[str, Any]:
    """Опубликовать пост из резерва клиента (kino_auto_db.reserve)."""
    item = auto_db.take_reserve(user_id)
    if not item:
        return {"ok": False, "error": "резерв пуст"}

    from kino.publish import publish as kino_publish

    published_any = False
    for ch in channels:
        try:
            ok = kino_publish(
                kino_cfg,
                item.get("caption", ""),
                item.get("image_url", ""),
                dry_run=False,
                kind=item.get("kind", "text") or "text",
                link_url=item.get("link", ""),
                bot_token=token,
                channel_chat_id=str(ch),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("резерв: публикация user_id=%d в %s не удалась: %s",
                             user_id, ch, exc)
            continue
        published_any = published_any or bool(ok)

    if published_any:
        return {"ok": True, "published": True, "from_reserve": True}
    return {"ok": False, "error": "публикация из резерва не удалась"}
