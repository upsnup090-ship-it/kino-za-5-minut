"""Регрессии узлов статьи: HTML от модели не должен попадать в текст.

Найдено после боевого прогона: `_text_to_nodes` разворачивал сущности, но
оставлял теги, и в статье на graph.org было видно литеральное `<b>жирный</b>`.
"""

from __future__ import annotations

from kino.article import _text_to_nodes


def test_html_tags_are_stripped():
    nodes = _text_to_nodes("<b>жирный</b> & тест")
    assert nodes == [{"tag": "p", "children": ["жирный & тест"]}]


def test_entities_are_unescaped():
    nodes = _text_to_nodes("a &amp; b &lt;3")
    assert nodes[0]["children"][0] == "a & b <3"


def test_paragraphs_split_on_newlines():
    nodes = _text_to_nodes("Привет\n\nВторой абзац")
    assert len(nodes) == 2
    assert nodes[0]["children"] == ["Привет"]
    assert nodes[1]["children"] == ["Второй абзац"]


def test_empty_text_gives_placeholder_node():
    nodes = _text_to_nodes("")
    assert nodes == [{"tag": "p", "children": [""]}]


def test_whitespace_only_lines_are_skipped():
    nodes = _text_to_nodes("А\n\n   \n\nБ")
    assert len(nodes) == 2


def test_link_tag_is_stripped_but_text_kept():
    nodes = _text_to_nodes('<a href="http://x">ссылка</a>')
    assert nodes[0]["children"] == ["ссылка"]