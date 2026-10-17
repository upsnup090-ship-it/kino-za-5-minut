"""Сборка подписи поста."""

from __future__ import annotations

import html


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