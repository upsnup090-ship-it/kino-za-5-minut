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

# --- мусорные страницы и мелкие превью (боевой прогон 18.10) -----------------


def test_pick_page_returns_none_on_zero_overlap():
    """Фото школы вместо фильма хуже отсутствия картинки.

    Боевой кейс: запрос «Что принадлежит другим фильм», поиск вернул
    Schule Nummer 1 (панорама школы) — ни одного общего слова с запросом.
    """
    pages = [
        _page("Schule_Nummer_1", "панорама школы, здание"),
        _page("Городская_школа_номер_один", "здание городской школы"),
    ]
    assert _pick_page(pages, "Что принадлежит другим фильм") is None


def test_thumb_width_parses_from_url():
    from kino.image import _thumb_width
    thumb = ("https://upload.wikimedia.org/wikipedia/commons/thumb/0/06/"
             "F.jpg/60px-F.jpg")
    plain = "https://upload.wikimedia.org/wikipedia/commons/a/ab/F.jpg"
    assert _thumb_width(thumb) == 60
    assert _thumb_width(plain) > 10**8  # не thumb — считаем крупным


def test_upgrade_thumb_asks_wikimedia_for_bigger_version():
    from kino.image import _upgrade_thumb
    thumb = ("https://upload.wikimedia.org/wikipedia/commons/thumb/0/06/"
             "F.jpg/60px-F.jpg")
    upgraded = _upgrade_thumb(thumb)
    assert upgraded == thumb.replace("60px-F.jpg", "640px-F.jpg")
    # не-thumb URL и уже крупное — без изменений
    plain = "https://upload.wikimedia.org/wikipedia/commons/a/ab/F.jpg"
    assert _upgrade_thumb(plain) == plain
    big = thumb.replace("60px", "800px")
    assert _upgrade_thumb(big) == big


def test_search_thumb_below_320px_is_upgraded():
    """60px из поисковой выдачи перезапрашивается крупным (боевой кейс)."""
    from kino.image import _upgrade_thumb
    from kino.image import _thumb_width as tw
    thumb = ("https://upload.wikimedia.org/wikipedia/commons/thumb/0/06/"
             "F.jpg/60px-F.jpg")
    assert tw(_upgrade_thumb(thumb)) == 640
