"""Сборка подписи поста."""

from __future__ import annotations

import html
from typing import Any


def escape_html(text: str) -> str:
    """Экранирует спецсимволы HTML для parse_mode=HTML."""
    return html.escape(text, quote=False)


def truncate_caption(text: str, max_length: int, hard_limit: int) -> str:
    """Обрезает подпись: мягкий лимит, затем жёсткий."""
    if len(text) <= max_length:
        return text
    if len(text) <= hard_limit:
        return text
    return text[: hard_limit - 1] + "…"


def _ensure_list(value: Any) -> list[str]:
    """Приводит значение к списку строк."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _format_rating(rating: Any) -> str:
    """Форматирует рейтинг как X.X/5."""
    try:
        r = float(rating)
        r = max(1.0, min(5.0, r))
        return f"{r:.1f}/5"
    except (TypeError, ValueError):
        return ""


def build_caption_new(draft_data: dict, image_url: str, cfg: Any) -> str:
    """Собирает подпись в чистом журналистском стиле."""
    headline = escape_html(str(draft_data.get("headline", "")).strip())
    text = escape_html(str(draft_data.get("text", "")).strip())
    key_moment = escape_html(str(draft_data.get("key_moment", "")).strip())
    question = escape_html(str(draft_data.get("question", "")).strip())
    hashtags = _ensure_list(draft_data.get("hashtags"))

    blocks: list[str] = []

    # Заголовок
    if headline:
        blocks.append(headline)

    # Основной текст
    if text:
        blocks.append(text)

    # Ключевой момент (если есть)
    if key_moment:
        blocks.append(key_moment)

    # Вопрос
    if question:
        blocks.append("—————")
        blocks.append(f"❓ {question}")

    # Хэштеги
    if hashtags:
        tags = " ".join(f"#{h}" for h in hashtags[:3])
        blocks.append(tags)

    caption = "\n\n".join(blocks)
    return truncate_caption(
        caption,
        max_length=cfg.settings.caption_max_length,
        hard_limit=cfg.settings.caption_hard_limit,
    )


def build_caption_old(draft_data: dict, image_url: str, cfg: Any) -> str:
    """Старый формат подписи (обратная совместимость)."""
    headline = escape_html(str(draft_data.get("headline", "")).strip())
    text = escape_html(str(draft_data.get("text", "")).strip())
    question = escape_html(str(draft_data.get("question", "")).strip())

    blocks = [b for b in (headline, text) if b]
    if question:
        blocks.append("—————")
        blocks.append(f"❓ {question}")

    caption = "\n\n".join(blocks)
    return truncate_caption(
        caption,
        max_length=cfg.settings.caption_max_length,
        hard_limit=cfg.settings.caption_hard_limit,
    )


def build_caption(draft_data: dict, image_url: str, cfg: Any) -> str:
    """Автовыбор формата подписи по наличию новых полей."""
    if any(k in draft_data for k in ("genre", "rating", "details", "accents", "hashtags", "key_moment")):
        return build_caption_new(draft_data, image_url, cfg)
    return build_caption_old(draft_data, image_url, cfg)