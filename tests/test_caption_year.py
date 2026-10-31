"""Сверка года в подписи с датой материала (шаг 5 раунда 16).

Боевой дефект 31.10.2026: материал Variety от 31 октября, в подписи
«Материал Variety, 2024». Правило 20 промпта судьи просит снимать такой
fact_source, но модель правила выполняет не всегда, а пост в канале
отредактировать нельзя. Здесь — последний рубеж, код.

Границы решения:
- нет даты материала -> сверка выключена (сравнивать не с чем);
- fact_source: любой расхождение года с датой материала — вырезать;
- text/key_moment: допуск 2 года, потому что «премьера в 2027» на материале
  2026-го — законный год релиза, а не выдумка;
- quote.date и announcement.date не трогаются: это дата события, а не
  материала, и материал может быть старее события на годы.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from kino.caption import _pub_year, _strip_wrong_year, build_caption


def _cfg():
    return SimpleNamespace(
        settings=SimpleNamespace(
            caption_max_length=900,
            caption_hard_limit=1000,
        )
    )


# --- _pub_year --------------------------------------------------------------


def test_pub_year_from_human_date():
    assert _pub_year("31 октября 2026") == 2026


def test_pub_year_none_without_date():
    assert _pub_year("") is None
    assert _pub_year("(неизвестна)") is None


# --- fact_source: допуск 0 --------------------------------------------------


def test_strips_wrong_year_from_fact_source():
    """Тот самый боевой кейс: Variety, 2024 на материале от 31.10.2026."""
    result = _strip_wrong_year("Variety, 2024", "31 октября 2026", "fact_source", 0)
    assert result == "Variety"
    assert "2024" not in result


def test_keeps_matching_year():
    result = _strip_wrong_year("Variety, 2026", "31 октября 2026", "fact_source", 0)
    assert result == "Variety, 2026"


def test_keeps_year_without_date():
    """Без даты материала не трогаем: вырезать наугад хуже, чем оставить."""
    result = _strip_wrong_year("Variety, 2024", "", "fact_source", 0)
    assert result == "Variety, 2024"


def test_keeps_year_when_date_is_placeholder():
    result = _strip_wrong_year("Variety, 2024", "(неизвестна)", "fact_source", 0)
    assert result == "Variety, 2024"


def test_strips_year_with_russian_preposition():
    result = _strip_wrong_year("по данным Variety в 2024 году", "31 октября 2026",
                               "fact_source", 0)
    assert "2024" not in result
    assert "Variety" in result


def test_strips_multiple_wrong_years():
    result = _strip_wrong_year("интервью 2019, Variety, 2024", "31 октября 2026",
                               "fact_source", 0)
    assert "2019" not in result
    assert "2024" not in result
    assert "Variety" in result


# --- text: допуск 2 ---------------------------------------------------------


def test_keeps_future_release_year_in_text():
    """«премьера в 2027» на материале 2026 — законный год релиза."""
    text = "Премьера фильма намечена на 2027."
    result = _strip_wrong_year(text, "31 октября 2026", "text", 2)
    assert result == text


def test_strips_old_year_in_text():
    text = "Актёр рассказывал об этом ещё в 2019 году."
    result = _strip_wrong_year(text, "31 октября 2026", "text", 2)
    assert "2019" not in result


def test_keeps_year_at_tolerance_boundary():
    """2024 и 2026 — расхождение 2, ещё в допуске для text."""
    text = "Съёмки начались в 2024."
    result = _strip_wrong_year(text, "31 октября 2026", "text", 2)
    assert "2024" in result


# --- лог --------------------------------------------------------------------


def test_logs_warning_on_strip(caplog):
    with caplog.at_level(logging.WARNING, logger="kino.caption"):
        _strip_wrong_year("Variety, 2024", "31 октября 2026", "fact_source", 0)
    assert any("2024" in r.message and "fact_source" in r.message for r in caplog.records)


def test_no_warning_when_year_matches(caplog):
    with caplog.at_level(logging.WARNING, logger="kino.caption"):
        _strip_wrong_year("Variety, 2026", "31 октября 2026", "fact_source", 0)
    assert not [r for r in caplog.records if "год" in r.message]


# --- сквозной build_caption -------------------------------------------------


def test_build_caption_strips_wrong_year_end_to_end():
    draft = {
        "headline": "Неожиданный эпизод «Дерри» вышел на HBO Max",
        "text": "Вышел внеплановый эпизод сериала.",
        "fact_source": "Variety, 2024",
    }
    caption = build_caption(draft, "", _cfg(), pub_date="31 октября 2026")
    assert "📚 Variety" in caption
    assert "2024" not in caption


def test_build_caption_keeps_year_without_pub_date():
    """Обратная совместимость: вызов без pub_date ничего не меняет."""
    draft = {"headline": "Ш", "text": "Т", "fact_source": "Variety, 2024"}
    assert "2024" in build_caption(draft, "", _cfg())
    assert "2024" in build_caption(draft, "", _cfg(), pub_date="")


def test_build_caption_keeps_quote_date():
    """Дата цитаты — дата события, не материала. Не трогаем."""
    draft = {
        "headline": "Ш",
        "text": "Т",
        "quote": {"speaker": "Дени Вильнёв", "occasion": "Канны", "date": "май 2019"},
    }
    caption = build_caption(draft, "", _cfg(), pub_date="31 октября 2026")
    assert "2019" in caption


def test_build_caption_keeps_announcement_date():
    """Дата премьеры может быть на годы впереди материала."""
    draft = {
        "headline": "Ш",
        "text": "Т",
        "announcement": {"date": "15 ноября 2029", "platform": "Netflix"},
    }
    caption = build_caption(draft, "", _cfg(), pub_date="31 октября 2026")
    assert "2029" in caption


# --- хвосты после вырезания -------------------------------------------------


def test_no_dangling_comma_after_strip():
    """«интервью 2019, Variety, 2024» -> «интервью, Variety», без «, ,»."""
    result = _strip_wrong_year("интервью 2019, Variety, 2024", "31 октября 2026",
                               "fact_source", 0)
    assert result == "интервью, Variety"
    assert ",," not in result
    assert not result.endswith(",")


def test_keeps_sentence_punctuation():
    """Точка в конце остаётся: вырезаем год, а не конец предложения."""
    result = _strip_wrong_year("Variety, 2024.", "31 октября 2026", "fact_source", 0)
    assert result == "Variety."
