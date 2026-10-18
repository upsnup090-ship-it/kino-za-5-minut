"""Регрессии подписи: обрезка сущностей, лимиты вариаций, хэштеги.

Пункты 1, 3, 5 из docs/todo.md.
"""

from __future__ import annotations

import re

import pytest

from kino.caption import (
    TELEGRAM_CAPTION_LIMIT,
    TELEGRAM_TEXT_LIMIT,
    _ensure_list,
    build_caption,
    caption_limit_for,
    normalize_hashtags,
    truncate_caption,
)
from kino.config import Config, Settings


def _cfg() -> Config:
    return Config(settings=Settings())


# --- п.1: разрез не должен попадать внутрь HTML-сущности ---------------------


@pytest.mark.parametrize("tail", ["&", "&am", "&amp", "&amp;", "&#12", "&#x1F", "&lt;"])
def test_truncate_never_leaves_broken_entity(tail):
    """Разрез ровно на сущности не должен оставлять висящий `&`."""
    text = "a" * 994 + tail + "b" * 50
    result = truncate_caption(text, max_length=950, hard_limit=1000)
    assert len(result) <= 1000
    assert not re.search(r"&[a-zA-Z#0-9]*$", result), f"обрубок сущности в хвосте: {result[-10:]!r}"


def test_truncate_never_leaves_open_tag():
    text = "a" * 995 + "<b>" + "b" * 50
    result = truncate_caption(text, max_length=950, hard_limit=1000)
    assert not re.search(r"<[^>]*$", result)


def test_truncate_short_text_unchanged():
    assert truncate_caption("короткий текст", 950, 1000) == "короткий текст"


def test_truncate_respects_hard_limit():
    result = truncate_caption("y" * 5000, max_length=950, hard_limit=1000)
    assert len(result) <= 1000


def test_truncate_never_returns_empty_for_nonempty_input():
    assert truncate_caption("&amp;" * 400, max_length=950, hard_limit=1000) != ""


# --- п.3: лимит зависит от вариации -----------------------------------------


def test_caption_limit_for_photo_and_gallery():
    assert caption_limit_for("photo") == TELEGRAM_CAPTION_LIMIT
    assert caption_limit_for("gallery") == TELEGRAM_CAPTION_LIMIT
    assert caption_limit_for("article") == TELEGRAM_CAPTION_LIMIT


def test_caption_limit_for_text_variations():
    assert caption_limit_for("text") == TELEGRAM_TEXT_LIMIT
    assert caption_limit_for("link") == TELEGRAM_TEXT_LIMIT
    assert caption_limit_for("poll") == TELEGRAM_TEXT_LIMIT


def test_telegram_limits_match_api():
    """Подпись к фото — 1024, текст сообщения — 4096."""
    assert TELEGRAM_CAPTION_LIMIT == 1024
    assert TELEGRAM_TEXT_LIMIT == 4096


def test_photo_caption_fits_telegram_limit():
    """Длинный черновик в вариации photo укладывается в лимит подписи."""
    draft = {
        "headline": "Шапка " * 40,
        "text": "Текст поста. " * 200,
        "key_moment": "🔥 Момент. " * 40,
        "question": "Вопрос?",
        "hashtags": ["кино", "премьера"],
    }
    caption = build_caption(draft, "", _cfg())
    assert len(caption) <= TELEGRAM_CAPTION_LIMIT


# --- п.5: хэштеги без мусора ------------------------------------------------


def test_ensure_list_drops_dicts_and_none():
    assert _ensure_list([{"a": 1}, None, "x", "  ", 5]) == ["x", "5"]


def test_ensure_list_drops_bools():
    assert _ensure_list([True, False, "x"]) == ["x"]


def test_ensure_list_scalar_and_none():
    assert _ensure_list(None) == []
    assert _ensure_list("x") == ["x"]
    assert _ensure_list({"a": 1}) == []


def test_normalize_hashtags_strips_hash_and_spaces():
    assert normalize_hashtags(["#кино", "новый фильм"]) == ["кино", "новый_фильм"]


def test_normalize_hashtags_dedupes_case_insensitively():
    assert normalize_hashtags(["Кино", "кино", "КИНО"]) == ["Кино"]


def test_normalize_hashtags_limits_to_three():
    assert len(normalize_hashtags(["a", "b", "c", "d", "e"])) == 3


def test_normalize_hashtags_skips_empty_and_punctuation_only():
    assert normalize_hashtags(["", "   ", "###", "!!!", "ок"]) == ["ок"]


def test_caption_hashtags_have_no_double_hash():
    caption = build_caption({"headline": "Ш", "text": "Т", "hashtags": ["#кино"]}, "", _cfg())
    assert "##" not in caption
    assert "#кино" in caption


def test_caption_never_shows_none_in_hashtags():
    caption = build_caption(
        {"headline": "Ш", "text": "Т", "hashtags": [None, 5, {"a": 1}]},
        "",
        _cfg(),
    )
    assert "None" not in caption
    assert "{'a': 1}" not in caption