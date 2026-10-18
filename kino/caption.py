"""Сборка подписи поста.

Обрезка подписи — не просто срез строки: текст уже HTML-экранирован, поэтому
разрез может попасть внутрь сущности (`&amp;`) и Telegram отобьёт сообщение с
`can't parse entities`. Здесь обрезка идёт по границе блока, а хвост после неё
чистится от незакрытых сущностей и тегов.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Лимиты Telegram: подпись к фото/медиагруппе — 1024, текст сообщения — 4096.
TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_TEXT_LIMIT = 4096

# Незакрытая сущность в хвосте: &, &am, &amp, &#12, &#x1F.
_OPEN_ENTITY_RE = re.compile(r"&[a-zA-Z#0-9]*$")
# Незакрытый тег в хвосте: <b, </b, <a href="...
_OPEN_TAG_RE = re.compile(r"<[^>]*$")


def escape_html(text: str) -> str:
    """Экранирует спецсимволы HTML для parse_mode=HTML."""
    return html.escape(text, quote=False)


def _heal_tail(text: str) -> str:
    """Убирает из хвоста незакрытую HTML-сущность или тег.

    Вызывается после обрезки: срез мог разрубить `&amp;` пополам, и тогда
    Telegram получит невалидную сущность и отклонит весь пост.
    """
    healed = _OPEN_ENTITY_RE.sub("", text)
    healed = _OPEN_TAG_RE.sub("", healed)
    return healed


def _cut_at_boundary(text: str, limit: int) -> str:
    """Обрезает текст до limit, стараясь резать по границе блока или предложения.

    Порядок попыток: двойной перевод строки (граница блока в подписи) →
    конец предложения → конец строки → пробел. Если ничего не нашлось,
    режем жёстко.
    """
    if len(text) <= limit:
        return text
    window = text[:limit]
    # Граница блока: ближайший разделитель "\n\n" в последней трети.
    floor = max(0, limit // 2)
    for separator in ("\n\n", ". ", "! ", "? ", "\n", " "):
        position = window.rfind(separator, floor)
        if position > 0:
            return window[: position + len(separator)].rstrip()
    return window


def truncate_caption(text: str, max_length: int, hard_limit: int) -> str:
    """Обрезает подпись: мягкий лимит, затем жёсткий.

    Режет по границе блока, а после обрезки лечит хвост от разрубленных
    сущностей и тегов. Результат всегда валиден для parse_mode=HTML.
    """
    if len(text) <= max_length:
        return text

    budget = min(max_length, hard_limit)
    # Место под многоточие, чтобы итог не вылез за лимит.
    cut = _cut_at_boundary(text, budget - 1)
    healed = _heal_tail(cut).rstrip()

    if not healed:
        # Обрезка съела всё (текст был из одних сущностей) — не отдаём пустоту.
        logger.warning("подпись выродилась в пустую строку при обрезке")
        return ""

    if len(healed) >= len(text):
        return healed
    result = healed + "…"
    # Хвост после добавления многоточия тоже может содержать сущность.
    return _heal_tail(result) if len(result) <= budget else healed


def _ensure_list(value: Any) -> list[str]:
    """Приводит значение к списку строк.

    Берём только скаляры: None, словари и списки внутри списка — это мусор от
    модели, и в подписи он выглядел как `#None` и `#5`.
    """
    if value is None:
        return []
    if isinstance(value, list):
        items: list[str] = []
        for entry in value:
            if isinstance(entry, (str, int, float)) and not isinstance(entry, bool):
                text = str(entry).strip()
                if text:
                    items.append(text)
        return items
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value).strip()
        return [text] if text else []
    return []


def normalize_hashtags(value: Any, limit: int = 3) -> list[str]:
    """Готовит хэштеги к подписи: без `#`, пробелы → `_`, дедуп, не больше limit.

    Модель регулярно возвращает `#кино` (промпт просит без `#`) или фразу с
    пробелом — Telegram такой хэштег не распознаёт.
    """
    result: list[str] = []
    seen: set[str] = set()
    for raw in _ensure_list(value):
        tag = raw.lstrip("#").strip()
        tag = re.sub(r"\s+", "_", tag)
        tag = re.sub(r"[^\w\-_]+", "", tag, flags=re.UNICODE)
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
        if len(result) >= limit:
            break
    return result


def _format_rating(rating: Any) -> str:
    """Форматирует рейтинг как X.X/5."""
    try:
        r = float(rating)
        r = max(1.0, min(5.0, r))
        return f"{r:.1f}/5"
    except (TypeError, ValueError):
        return ""


def build_caption(draft_data: dict, image_url: str, cfg: Any) -> str:
    """Собирает подпись в чистом журналистском стиле.

    Стиль один: заголовок, текст, ключевой момент, вопрос, хэштеги. Старый
    вариант подписи убран — он отличался только отсутствием key_moment и
    переключался по наличию ключа в словаре, а не по значению.
    """
    headline = escape_html(str(draft_data.get("headline", "") or "").strip())
    text = escape_html(str(draft_data.get("text", "") or "").strip())
    key_moment = escape_html(str(draft_data.get("key_moment", "") or "").strip())
    question = escape_html(str(draft_data.get("question", "") or "").strip())
    hashtags = normalize_hashtags(draft_data.get("hashtags"))

    blocks: list[str] = []

    if headline:
        blocks.append(headline)
    if text:
        blocks.append(text)
    if key_moment:
        blocks.append(key_moment)
    if question:
        blocks.append("—————")
        blocks.append(f"❓ {question}")
    if hashtags:
        blocks.append(" ".join(f"#{h}" for h in hashtags))

    caption = "\n\n".join(blocks)
    return truncate_caption(
        caption,
        max_length=cfg.settings.caption_max_length,
        hard_limit=cfg.settings.caption_hard_limit,
    )


def caption_limit_for(kind: str) -> int:
    """Лимит длины подписи для вариации поста.

    Подпись к фото и медиагруппе Telegram режет на 1024 символах, текст
    сообщения — на 4096. Раньше везде применялся общий лимит 1000, из-за чего
    текстовые вариации резались без причины.
    """
    if kind in ("text", "link", "poll"):
        return TELEGRAM_TEXT_LIMIT
    return TELEGRAM_CAPTION_LIMIT