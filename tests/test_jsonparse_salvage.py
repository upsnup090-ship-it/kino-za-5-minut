"""Регрессии спасения обрезанного JSON, включая массивы.

Пункт 12 из docs/todo.md: строковые поля спасались, массивы — нет, из-за чего
в обрезанном ответе терялись hashtags и всё, что шло после них.
"""

from __future__ import annotations

import pytest

from kino.jsonparse import parse_json_object, salvage_partial


def test_salvage_string_fields():
    result = parse_json_object('{"headline":"A","text":"B"', salvage_keys=("headline", "text"))
    assert result == {"headline": "A", "text": "B"}


def test_salvage_array_field_closed():
    result = parse_json_object(
        '{"headline":"A","hashtags":["x","y"]', salvage_keys=("headline", "hashtags")
    )
    assert result["hashtags"] == ["x", "y"]


def test_salvage_array_field_truncated():
    """Ответ оборвался внутри массива — часть элементов всё равно нужна."""
    result = parse_json_object(
        '{"headline":"A","hashtags":["x","y"', salvage_keys=("headline", "hashtags")
    )
    assert result["hashtags"] == ["x", "y"]


def test_salvage_empty_array_is_skipped():
    result = parse_json_object('{"headline":"A","hashtags":[]', salvage_keys=("headline", "hashtags"))
    assert result == {"headline": "A"}


def test_salvage_array_with_escapes():
    result = salvage_partial('{"h":["x\\ny","z"]', ("h",))
    assert result == {"h": ["x\ny", "z"]}


def test_salvage_string_with_escaped_quote():
    result = salvage_partial('{"text":"стр\\"ока"', ("text",))
    assert result == {"text": 'стр"ока'}


def test_complete_json_still_parses_normally():
    result = parse_json_object('{"headline":"A","hashtags":["x"]}')
    assert result == {"headline": "A", "hashtags": ["x"]}


def test_json_in_markdown_fence():
    result = parse_json_object('```json\n{"headline":"A"}\n```')
    assert result == {"headline": "A"}


def test_raw_newlines_inside_string_are_escaped():
    result = parse_json_object('{"text":"a\nb"}')
    assert result == {"text": "a\nb"}


def test_no_json_raises():
    with pytest.raises(ValueError):
        parse_json_object("нет JSON тут")


def test_empty_content_raises():
    with pytest.raises(ValueError):
        parse_json_object("")


def test_draft_salvage_keys_include_hashtags():
    from kino.draft import _DRAFT_SALVAGE_KEYS

    assert "hashtags" in _DRAFT_SALVAGE_KEYS


def test_judge_salvage_keys_include_hashtags_and_reason():
    from kino.judge import _SALVAGE_KEYS

    assert "hashtags" in _SALVAGE_KEYS
    assert "reason" in _SALVAGE_KEYS