"""Резерв: посты, которые уже собраны и проверены судьёй, но не опубликованы.

Зачем. В режиме слотов пустой слот означает тишину в канале. Резерв — это
страховка от сбоя, а не основной источник: пост попадает туда, только если
он уже готов, а слот занят другим. Второй черновик заранее не генерируется
(решение владельца: лишние токены на каждый слот).

Модуль связывает готовый пост и таблицу `reserve` в `kino.db`. Публикация
из резерва живёт в `kino/serve.py` — здесь только хранение.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from kino.config import Config
from kino.schedule_db import ScheduleDb, ScheduleDbError

logger = logging.getLogger(__name__)


def store(
    cfg: Config,
    *,
    link: str,
    title: str,
    source: str,
    format: str,
    draft_data: dict[str, Any],
    caption: str,
    image_url: str,
    kind: str,
) -> bool:
    """Кладёт готовый пост в резерв. False — не влез или такая ссылка есть.

    Ошибки БД не пробрасываются: резерв вторичен по отношению к уже
    вышедшему посту, и его недоступность не должна ломать прогон.
    """
    if not link:
        return False
    try:
        db = ScheduleDb(cfg.db_path)
        reserved_id = db.add_reserve(
            link=link,
            title=title,
            source=source,
            format=format,
            draft_json=json.dumps(draft_data, ensure_ascii=False),
            caption=caption,
            image_url=image_url,
            kind=kind,
            ttl_hours=cfg.settings.reserve_ttl_hours,
            max_items=cfg.settings.reserve_max_items,
        )
    except ScheduleDbError as exc:
        logger.warning("резерв: не сохранён (%s)", exc)
        return False
    if reserved_id is None:
        logger.debug("резерв: не добавлен (полон или ссылка уже есть): %s", link)
        return False
    logger.info("резерв: пост сохранён (%s, id=%d)", title[:60], reserved_id)
    return True


def stats(cfg: Config) -> tuple[int, int]:
    """(доступно, всего) — для админки и дневного итога."""
    try:
        db = ScheduleDb(cfg.db_path)
        available = len(db.list_reserve(only_available=True))
        total = len(db.list_reserve())
    except ScheduleDbError as exc:
        logger.warning("резерв: статистика недоступна (%s)", exc)
        return 0, 0
    return available, total


def purge(cfg: Config) -> int:
    """Чистит использованные и просроченные посты. Возвращает число."""
    try:
        return ScheduleDb(cfg.db_path).purge_reserve()
    except ScheduleDbError as exc:
        logger.warning("резерв: чистка не удалась (%s)", exc)
        return 0
