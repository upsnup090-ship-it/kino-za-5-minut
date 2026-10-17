"""Тесты источника Horizon: парсинг дайджеста и выбор файла."""

from pathlib import Path

import pytest

from kino.horizon import (
    HorizonError,
    _detect_lang,
    _first_url,
    _parse_date_from_name,
    latest_digest,
    parse_digest,
)


def test_parse_digest_sections():
    md = """# Дайджест

## Первая новость
Текст первой новости. [Читать](https://example.com/1)

## Вторая новость
Текст второй новости. https://example.com/2
"""
    items = parse_digest(md)
    assert len(items) == 2
    assert items[0].title == "Первая новость"
    assert items[0].link == "https://example.com/1"
    assert items[1].title == "Вторая новость"
    assert items[1].link == "https://example.com/2"


def test_parse_digest_english():
    md = """## Dune Part Three announced
Denis Villeneuve confirms the sequel. [Source](https://variety.com/dune)
"""
    items = parse_digest(md)
    assert len(items) == 1
    assert items[0].title == "Dune Part Three announced"
    assert items[0].link == "https://variety.com/dune"
    assert items[0].source == "variety.com"


def test_parse_digest_chinese():
    md = """## 沙丘第三部宣布
丹尼斯·维伦纽瓦确认续集。来源: https://example.com/dune
"""
    items = parse_digest(md)
    assert len(items) == 1
    assert items[0].title == "沙丘第三部宣布"
    assert items[0].link == "https://example.com/dune"


def test_parse_digest_no_sections_single_item():
    md = "Одна новость без заголовков. https://example.com/only"
    items = parse_digest(md)
    assert len(items) == 1
    assert items[0].link == "https://example.com/only"


def test_parse_digest_empty():
    assert parse_digest("") == []
    assert parse_digest("   \n  ") == []


def test_detect_lang():
    assert _detect_lang("## 沙丘") == "zh"
    assert _detect_lang("## Dune Part Three") == "en"
    assert _detect_lang("## 12345") == "unknown"


def test_first_url_prefers_markdown():
    block = "[текст](https://a.com/x) и голая https://b.com/y"
    assert _first_url(block) == "https://a.com/x"


def test_first_url_bare():
    assert _first_url("просто https://b.com/y") == "https://b.com/y"


def test_parse_date_from_name():
    assert _parse_date_from_name(Path("2026-10-17.md")) > 0
    assert _parse_date_from_name(Path("digest_20261017.md")) > 0
    assert _parse_date_from_name(Path("no_date.md")) == 0.0


def test_latest_digest_picks_newest(tmp_path):
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "b.md").write_text("b", encoding="utf-8")
    # b.md создан позже — должен быть выбран.
    assert latest_digest(tmp_path).name == "b.md"


def test_latest_digest_explicit(tmp_path):
    target = tmp_path / "custom.md"
    target.write_text("x", encoding="utf-8")
    assert latest_digest(tmp_path, explicit=target) == target


def test_latest_digest_missing_dir(tmp_path):
    with pytest.raises(HorizonError):
        latest_digest(tmp_path / "nope")


def test_latest_digest_empty_dir(tmp_path):
    with pytest.raises(HorizonError):
        latest_digest(tmp_path)