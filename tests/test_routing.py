"""Маршрутизация формата по типу материала (Фаза 1).

Док: docs/source-routing-design.md. Модуль пока нигде не вызывается — эти тесты
и есть контракт, на котором строится подключение в `choose.py` (Фаза 4).
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
import yaml

from kino import routing
from kino.collect import FeedItem

ROOT = Path(__file__).resolve().parents[1]


def _item(title: str = "", snippet: str = "", link: str = "", video: str = "") -> FeedItem:
    return FeedItem(source="s", title=title, link=link, pub_date="", ts=0,
                    snippet=snippet, video_url=video)


# --- detect_kinds -------------------------------------------------------------

def test_feed_kind_video_gives_video_kind():
    kinds = routing.detect_kinds(_item("Some random upload"), "video")
    assert "video" in kinds


def test_feed_kind_unknown_falls_back_to_news():
    assert routing.detect_kinds(_item("Ничего не похоже"), "какой-то мусор") == {"news"}


def test_review_headline_detected():
    assert "review" in routing.detect_kinds(
        _item("Dune Part Three review: a visual masterpiece"), "mixed")


def test_review_plural_detected():
    """Префиксные маркеры: «reviews» ловится маркером «review*»."""
    assert "review" in routing.detect_kinds(_item("The best reviews of 2026"), "mixed")


def test_review_marker_does_not_match_preview():
    """Граница слова слева: «preview» не про «review»."""
    assert "review" not in routing.detect_kinds(_item("A preview of things"), "mixed")


def test_release_date_detected():
    kinds = routing.detect_kinds(_item("Стала известна дата премьеры фильма"), "mixed")
    assert "release_date" in kinds


def test_russian_morphology_via_prefix():
    """«номинации»/«номинация» — один маркер «номинац*»."""
    for word in ("номинации", "номинация", "номинаций"):
        assert "festival" in routing.detect_kinds(_item(f"Оскар: {word}")), word


def test_festival_detected():
    assert "festival" in routing.detect_kinds(_item("Oscar 2027: early frontrunners"), "mixed")


def test_interview_and_production_detected():
    kinds = routing.detect_kinds(
        _item("Интервью: Вильнёв рассказал о съёмках", "Режиссёр поделился деталями"), "mixed")
    assert {"interview", "production"} <= kinds


def test_archive_detected():
    assert "archive" in routing.detect_kinds(
        _item("10 малоизвестных фактов о «Бегущем по лезвию»"), "mixed")


def test_plain_news_has_only_news_kind():
    item = _item("Студия объявила о проекте", "О деталях станет известно позже")
    assert routing.detect_kinds(item, "mixed") == {"news"}


def test_video_url_adds_video_and_trailer():
    kinds = routing.detect_kinds(_item("Что-то", video="https://youtu.be/x"), "mixed")
    assert {"video", "trailer"} <= kinds


# --- detect_signals -----------------------------------------------------------

@pytest.mark.parametrize("text", [
    "премьера 15 ноября 2026 года",
    "релиз ноябрь 2026",
    "в прокате с 15.11.2026",
    "окно — лето 2027",
    "release date is set",
])
def test_date_signal(text):
    assert "date" in routing.detect_signals(_item(text))


def test_bare_year_is_not_a_date():
    """«Oscar 2027» — год церемонии, а не дата релиза: `when` на такое не пускаем."""
    assert "date" not in routing.detect_signals(_item("Oscar 2027 race"))


@pytest.mark.parametrize("text", [
    "собрал $82 млн",
    "бюджет 200 млн рублей",
    "оценка 8/10",
    "4 из 5 звёзд",
    "рост на 12%",
])
def test_number_signal(text):
    assert "number" in routing.detect_signals(_item(text))


def test_plain_digit_is_not_a_number_signal():
    assert "number" not in routing.detect_signals(_item("Dune 2 в прокате"))


def test_speaker_signal_from_quotes_in_snippet():
    item = _item("Вильнёв о финале",
                 'Режиссёр сказал: «Мне было важно закрыть арку Поля на этой ноте»')
    assert "speaker" in routing.detect_signals(item)


def test_speaker_signal_from_verb_in_title():
    assert "speaker" in routing.detect_signals(_item("Круз рассказал о трюках"))


def test_speaker_not_from_verb_in_snippet_only():
    """Регресс сессии 1: пересказ издателя в сниппете цитатой не считается."""
    item = _item("Denis Villeneuve returns to direct Dune Part Three",
                 "The director confirmed the sequel is in development")
    assert "speaker" not in routing.detect_signals(item)


def test_reporting_verbs_are_not_speech():
    """confirmed/revealed/reportedly — слова издателя, не атрибуируемая речь."""
    for title in ("Studio confirms sequel", "Trailer revealed today", "Reportedly in works"):
        assert "speaker" not in routing.detect_signals(_item(title)), title


def test_video_signal_from_link():
    assert "video" in routing.detect_signals(
        _item("Смотрите", link="https://youtube.com/watch?v=x"))


def test_video_signal_from_video_url():
    assert "video" in routing.detect_signals(_item("Ролик", video="https://youtu.be/x"))


# --- allowed_formats ----------------------------------------------------------

def test_news_allowed_for_any_kind():
    assert "news" in routing.allowed_formats({"trailer"}, set())
    assert "news" in routing.allowed_formats(set(), set())


def test_when_requires_date():
    assert "when" not in routing.allowed_formats({"release_date"}, set())
    assert "when" in routing.allowed_formats({"release_date"}, {"date"})


def test_announcement_requires_date():
    assert "announcement" not in routing.allowed_formats({"news"}, set())
    assert "announcement" in routing.allowed_formats({"news"}, {"date"})


def test_trailer_requires_video():
    assert "trailer" not in routing.allowed_formats({"trailer"}, set())
    assert "trailer" in routing.allowed_formats({"trailer"}, {"video"})


def test_facts_not_allowed_on_plain_news():
    """Главный провал сессии 1: facts на свежей новости = пересказ."""
    assert "facts" not in routing.allowed_formats({"news"}, set())


def test_facts_allowed_on_archive():
    assert "facts" in routing.allowed_formats({"archive"}, set())


def test_infographic_requires_number():
    assert "infographic" not in routing.allowed_formats({"boxoffice"}, set())
    assert "infographic" in routing.allowed_formats({"boxoffice"}, {"number"})


def test_quote_not_allowed_without_speaker():
    assert "quote" not in routing.allowed_formats({"interview"}, set())
    assert "quote" in routing.allowed_formats({"interview"}, {"speaker"})


def test_empty_intersection_returns_fallback():
    custom = {"fallback": "news",
              "when": {"kinds": ["release_date"], "require": ["date"]}}
    assert routing.allowed_formats(set(), set(), custom) == ["news"]


def test_custom_routing_overrides_default():
    custom = {"fallback": "news", "facts": {"kinds": ["news"], "require": []}}
    assert routing.allowed_formats({"news"}, set(), custom) == ["facts"]


def test_allowed_formats_is_stable_order():
    first = routing.allowed_formats({"news"}, set())
    second = routing.allowed_formats({"news"}, set())
    assert first == second


# --- pick_format --------------------------------------------------------------

def test_pick_never_returns_disallowed():
    rng = random.Random(42)
    weights = {"facts": 20, "news": 15, "when": 10, "quote": 6}
    allowed = ["news", "when"]
    picked = {routing.pick_format(weights, allowed, random_func=rng.random)
              for _ in range(1000)}
    assert picked <= set(allowed)


def test_disallowed_high_weight_cannot_win():
    rng = random.Random(1)
    for _ in range(500):
        assert routing.pick_format({"facts": 100, "news": 1}, ["news"],
                                   random_func=rng.random) == "news"


def test_pick_renormalizes_weights_inside_allowed():
    rng = random.Random(7)
    counts = {"a": 0, "b": 0}
    for _ in range(2000):
        counts[routing.pick_format({"a": 90, "b": 10}, ["a", "b"],
                                   random_func=rng.random)] += 1
    assert 1700 <= counts["a"] <= 1900


def test_pick_without_positive_weights_returns_first_allowed():
    assert routing.pick_format({}, ["when", "news"]) == "when"
    assert routing.pick_format({"facts": 5}, ["when", "news"]) == "when"


def test_pick_empty_allowed_returns_empty_string():
    assert routing.pick_format({"news": 1}, []) == ""


# --- реестр и дрейф -----------------------------------------------------------

def _config_weights() -> list[str]:
    data = yaml.safe_load((ROOT / "config" / "formats.yaml").read_text(encoding="utf-8"))
    return list(data["weights"])


def test_default_routing_covers_all_config_formats():
    """Каждый формат из weights имеет правило допуска — иначе он мёртв."""
    missing = [fmt for fmt in _config_weights() if fmt not in routing.DEFAULT_ROUTING]
    assert not missing, f"нет правил маршрутизации для {missing}"


def test_default_routing_kinds_are_known():
    for fmt, rule in routing.DEFAULT_ROUTING.items():
        if not isinstance(rule, dict):
            continue
        unknown = set(rule.get("kinds") or []) - set(routing.KINDS)
        assert not unknown, f"{fmt}: неизвестные классы {unknown}"


def test_default_routing_signals_are_known():
    for fmt, rule in routing.DEFAULT_ROUTING.items():
        if not isinstance(rule, dict):
            continue
        unknown = set(rule.get("require") or []) - set(routing.SIGNALS)
        assert not unknown, f"{fmt}: неизвестные требования {unknown}"


def test_every_kind_has_markers():
    """У всех классов, кроме базового `news`, есть маркеры.

    `news` — остаточный класс: он приходит априором фида, маркеров не требует.
    """
    assert set(routing._KIND_MARKERS) == set(routing.KINDS) - {"news"}


def test_fallback_is_in_routing_and_allowed():
    fallback = routing.DEFAULT_ROUTING["fallback"]
    assert fallback in routing.DEFAULT_ROUTING
    assert routing.allowed_formats(set(), set()) == [fallback] or \
        fallback in routing.allowed_formats(set(), set())