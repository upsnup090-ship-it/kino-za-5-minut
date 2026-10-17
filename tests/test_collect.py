"""Тесты сбора."""

from kino.collect import _clean_html, _extract_snippet


def test_clean_html_strips_tags_and_unescapes():
    assert _clean_html("<p>Hello &amp; goodbye</p>") == "Hello & goodbye"


def test_clean_html_collapses_whitespace():
    assert _clean_html("a   b\n\tc") == "a b c"


def test_extract_snippet_prefers_content_snippet():
    entry = {
        "contentSnippet": "короткий текст",
        "description": "длинное описание",
    }
    assert _extract_snippet(entry, 300) == "короткий текст"


def test_extract_snippet_truncates():
    entry = {"description": "x" * 500}
    assert len(_extract_snippet(entry, 300)) == 300


def test_extract_snippet_handles_list_content():
    entry = {"content": [{"value": "первый"}, {"value": "второй"}]}
    assert _extract_snippet(entry, 300) == "первый второй"