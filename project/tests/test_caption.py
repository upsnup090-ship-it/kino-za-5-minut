"""Тесты подписи."""

from kino.caption import escape_html, truncate_caption


def test_escape_html_escapes_special_chars():
    assert escape_html("<b>a & b</b>") == "&lt;b&gt;a &amp; b&lt;/b&gt;"


def test_truncate_caption_short_unchanged():
    text = "короткий текст"
    assert truncate_caption(text, max_length=950, hard_limit=1000) == text


def test_truncate_caption_soft_limit():
    text = "x" * 1200
    result = truncate_caption(text, max_length=950, hard_limit=1000)
    assert len(result) <= 1000
    assert result.endswith("…")


def test_truncate_caption_hard_limit():
    text = "y" * 2000
    result = truncate_caption(text, max_length=950, hard_limit=1000)
    assert len(result) <= 1000