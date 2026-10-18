"""Регрессии выбора статьи Wikipedia для картинки.

Найдено боевым прогоном: для поста про «Saturday Night Live UK» в канал ушла
фотография Джимми Фэллона. Причина — _pick_page доверял ранжированию
Wikipedia, а для составных запросов первыми идут персоны, не произведение.
"""

from __future__ import annotations

from kino.image import _clean_url, _pick_page, _wiki_query


def _page(key: str, description: str = "", title: str = "") -> dict:
    return {"key": key, "title": title or key, "description": description}


# --- выбор страницы ---------------------------------------------------------


def test_show_query_prefers_show_over_actor():
    """Запрос по названию передачи не должен давать фото актёра."""
    pages = [
        _page("Фэллон,_Джимми", "американский телеведущий, актёр, комик"),
        _page("Болдуин,_Алек", "американский актёр, комик"),
        _page("Saturday_Night_Live", "вечерняя музыкально-юмористическая передача"),
    ]
    assert _pick_page(pages, "Saturday Night Live UK")["key"] == "Saturday_Night_Live"


def test_talk_show_counts_as_work():
    pages = [
        _page("Кто-то", "актёр"),
        _page("Ночное_шоу", "американское ток-шоу"),
    ]
    assert _pick_page(pages, "Ночное шоу с кем-то")["key"] == "Ночное_шоу"


def test_first_page_kept_when_it_matches_query():
    """«Братья Коэн» — первая страница связана с запросом, отбор не срабатывает."""
    pages = [
        _page("Братья_Коэн", "американские кинорежиссёры, сценаристы и продюсеры"),
        _page("Фарго_(фильм)", "фильм 1996 года"),
    ]
    assert _pick_page(pages, "Братья Коэн")["key"] == "Братья_Коэн"


def test_fallback_prefers_best_overlap_not_first():
    """Если произведения нет, берём максимальное пересечение с запросом."""
    pages = [
        _page("Фэллон,_Джимми", "актёр"),
        _page("Джимми_Фэллон_шоу", "ток-шоу"),
    ]
    assert _pick_page(pages, "Фэллон Джимми шоу")["key"] == "Джимми_Фэллон_шоу"


def test_empty_pages_returns_none():
    assert _pick_page([], "что угодно") is None


def test_single_page_returned():
    pages = [_page("Дюна_(фильм)", "фильм 2021 года")]
    assert _pick_page(pages, "Дюна")["key"] == "Дюна_(фильм)"


# --- нормализация запроса и URL ---------------------------------------------


def test_wiki_query_extracts_title_from_url():
    title, lang = _wiki_query("https://ru.wikipedia.org/wiki/Дюна_(фильм)")
    assert title == "Дюна (фильм)"
    assert lang == "ru"


def test_wiki_query_plain_title():
    assert _wiki_query("Братья Коэн") == ("Братья Коэн", "")


def test_wiki_query_empty():
    assert _wiki_query("") == ("", "")


def test_clean_url_fixes_protocol_relative():
    assert _clean_url("//upload.wikimedia.org/x.jpg") == "https://upload.wikimedia.org/x.jpg"


def test_clean_url_replaces_thumb_alias():
    url = "https://thumb.wikimedia.org/wikipedia/commons/a/ab/X.jpg"
    assert _clean_url(url) == "https://upload.wikimedia.org/wikipedia/commons/a/ab/X.jpg"


def test_clean_url_drops_query_keeps_thumb_path():
    url = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/X.jpg/220px-X.jpg?foo=1"
    assert _clean_url(url) == "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/X.jpg/220px-X.jpg"


def test_clean_url_rejects_placeholder():
    assert _clean_url("https://example.com/no_image_available.png") == ""
    assert _clean_url("") == ""