"""Форматы 5–16: маппинг доставки, рендер формат-полей, фолбэки.

Закрывают находки review 18.10 (docs/todo.md): без этих тестов новые форматы
работали «на глаз» — pick_variation их не трогал, подпись поля не видела,
а run() вообще падал с UnboundLocalError.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kino.caption import build_caption
from kino.choose import Choice, _pick_format
from kino.collect import FeedItem
from kino.config import Settings
from kino.infographic import build_url, validate
from kino.judge import judge
from kino.pipeline import (
    _FORMAT_TO_KIND,
    _VARIATIONS,
    _gallery_images,
    _valid_url,
    pick_variation,
)

CFG = SimpleNamespace(settings=SimpleNamespace(caption_max_length=950, caption_hard_limit=1000))


def _item(**kw) -> FeedItem:
    base = dict(source="s", title="t", link="https://news.example/a",
                pub_date="", ts=0.0, snippet="", images=[], video_url="")
    base.update(kw)
    return FeedItem(**base)


def _choice(fmt: str, **item_kw) -> Choice:
    return Choice(item=_item(**item_kw), format=fmt, quality_score=5, sad_topic=False)


def _pcfg(variations: dict[str, int] | None = None) -> SimpleNamespace:
    return SimpleNamespace(settings=SimpleNamespace(
        post_variations=variations if variations is not None else {"photo": 1}))


# --- маппинг формата на вариацию доставки ------------------------------------

@pytest.mark.parametrize("fmt,kind", [
    ("video", "link"), ("trailer", "link"),
    ("infographic", "photo"),
    ("collage", "gallery"), ("comparison", "gallery"),
    ("interview", "article"), ("review", "article"), ("opinion", "article"),
    ("poll", "poll"),
])
def test_forced_formats_pick_their_variation(fmt, kind):
    assert pick_variation(_pcfg(), {}, "", _choice(fmt))[0] == kind


def test_free_formats_keep_random_variation():
    for fmt in ("facts", "news", "when", "quote", "behind_scenes",
                "announcement", "riddle"):
        assert pick_variation(_pcfg({"photo": 1}), {}, "", _choice(fmt))[0] == "photo"


# --- ссылка для video/trailer: сначала фид, потом черновик, потом новость ----

def test_link_prefers_feed_video_over_llm_video_url():
    choice = _choice("video", video_url="https://youtube.com/watch?v=feed")
    kind, url = pick_variation(_pcfg(), {"video_url": "https://youtube.com/watch?v=llm"},
                               "", choice)
    assert (kind, url) == ("link", "https://youtube.com/watch?v=feed")


def test_link_falls_back_to_draft_video_url_then_news_link():
    choice = _choice("video")
    assert pick_variation(_pcfg(), {"video_url": "https://youtu.be/llm"}, "", choice) == (
        "link", "https://youtu.be/llm")
    assert pick_variation(_pcfg(), {"video_url": "посмотри на YouTube"}, "", choice) == (
        "link", "https://news.example/a")


def test_link_without_any_url_falls_back_to_photo():
    choice = _choice("video", link="")
    assert pick_variation(_pcfg(), {}, "", choice) == ("photo", "")


def test_valid_url_accepts_only_http_links():
    assert _valid_url("https://a.b/c") == "https://a.b/c"
    assert _valid_url("ftp://a.b") == ""
    assert _valid_url("  ") == ""
    assert _valid_url(None) == ""


def test_random_link_variation_also_uses_feed_video():
    cfg = _pcfg({"link": 1})
    choice = _choice("news", video_url="https://youtube.com/watch?v=feed")
    assert pick_variation(cfg, {}, "", choice) == ("link", "https://youtube.com/watch?v=feed")


# --- карусель: дедуп с основной картинкой и фолбэк на фид --------------------

def test_gallery_dedups_main_image_and_keeps_order():
    with patch("kino.pipeline.find_images", return_value=[
            "https://wiki/a.jpg", "https://wiki/a.jpg", "https://wiki/b.jpg"]):
        urls = _gallery_images("w", {"film": "F", "director": "D"}, _choice("collage"),
                               "https://wiki/a.jpg", _pcfg())
    assert urls == ["https://wiki/b.jpg"]


def test_gallery_falls_back_to_feed_images():
    """Wiki отдала только main-картинку → не хватает кадров, берём из фида."""
    with patch("kino.pipeline.find_images", return_value=["https://wiki/main.jpg"]):
        choice = _choice("collage", images=["https://feed/1.jpg", "https://feed/2.jpg"])
        urls = _gallery_images("w", {}, choice, "https://wiki/main.jpg", _pcfg())
    assert urls == ["https://feed/1.jpg", "https://feed/2.jpg"]


# --- подпись: формат-поля доходят до поста ------------------------------------

def test_caption_renders_announcement_date_and_platform():
    cap = build_caption({
        "headline": "Дюна 3", "text": "Анонс",
        "announcement": {"date": "18 декабря 2026", "platform": "Netflix",
                         "what_to_expect": "Финал трилогии"},
    }, "", CFG)
    assert "18 декабря 2026" in cap and "Netflix" in cap and "Финал трилогии" in cap


def test_caption_renders_review_verdict_and_pros_cons():
    cap = build_caption({
        "headline": "Обзор", "text": "Текст",
        "review": {"verdict": "Смотреть", "rating": "8/10",
                   "pros": ["визуал"], "cons": ["темп"]},
    }, "", CFG)
    assert "Смотреть" in cap and "(8/10)" in cap and "визуал" in cap and "темп" in cap


def test_caption_renders_comparison_rows_with_winner():
    cap = build_caption({
        "headline": "Сравнение", "text": "Т",
        "comparison": {"item_a": "Дюна 2021", "item_b": "Дюна 1984",
                       "categories": [{"name": "Визуал", "winner": "a", "detail": "масштаб"}]},
    }, "", CFG)
    assert "Дюна 2021" in cap and "Визуал" in cap and "масштаб" in cap


def test_caption_shows_riddle_hint_but_never_answer():
    cap = build_caption({
        "headline": "Загадка", "text": "Т",
        "riddle": {"question_text": "Кто?", "hint": "Дебют 1992",
                   "answer": "Ди Каприо"},
    }, "", CFG)
    assert "Дебют 1992" in cap
    assert "Ди Каприо" not in cap


def test_caption_escapes_html_in_format_fields():
    cap = build_caption({
        "headline": "H", "text": "T",
        "review": {"verdict": "<script>злой</script>", "pros": [], "cons": []},
    }, "", CFG)
    assert "<script>" not in cap and "&lt;script&gt;" in cap


def test_caption_converts_markdown_bold_to_html():
    cap = build_caption({"headline": "H", "text": "**В:** вопрос **О:** ответ"}, "", CFG)
    assert "<b>В:</b>" in cap and "**" not in cap


def test_poll_caption_does_not_duplicate_question():
    draft = {"headline": "Опрос", "text": "Т", "question": "Какой фильм лучше?"}
    assert "❓" not in build_caption(draft, "", CFG, kind="poll")
    assert "❓" in build_caption(draft, "", CFG, kind="text")


def test_infographic_facts_stay_in_text_when_chart_failed():
    draft = {"headline": "Цифры", "text": "Т",
             "infographic": {"title": "Сборы", "facts": [{"label": "Бюджет", "value": "$200 млн"}]}}
    with_chart = build_caption(draft, "https://quickchart/x.png", CFG)
    failed = build_caption(draft, "https://wiki/photo.jpg", CFG, include_facts=True)
    assert "$200 млн" not in with_chart
    assert "$200 млн" in failed


# --- инфографика: валидный JSON и фолбэки ------------------------------------

def test_infographic_validate_requires_two_facts():
    assert validate(None) == ("", [])
    assert validate({"facts": [{"label": "A", "value": "1"}]}) == ("", [])
    title, facts = validate({"title": "T", "facts": [
        {"label": "A", "value": "1"}, {"label": "B", "value": "2"}, {"label": "", "value": ""}]})
    assert title == "T" and len(facts) == 2


def test_infographic_build_url_is_valid_json_without_js_strings():
    url = build_url("T", [{"label": "Бюджет", "value": "$200 млн"},
                          {"label": "Рейтинг", "value": "8.5"}])
    import urllib.parse
    config = json.loads(urllib.parse.unquote(url.split("c=")[1].split("&")[0]))
    assert "function(" not in json.dumps(config)
    assert all(v in lbl for lbl, v in zip(config["data"]["labels"],
                                          ["$200 млн", "8.5"]))


def test_infographic_mixed_scales_become_flat_bars():
    """$200 млн и 8.5/10 на одной шкале — фальшивая диаграмма: бары ровные."""
    import urllib.parse
    url = build_url("T", [{"label": "Сборы", "value": "$712 млн"},
                          {"label": "Рейтинг", "value": "8.5/10"}])
    config = json.loads(urllib.parse.unquote(url.split("c=")[1].split("&")[0]))
    assert len(set(config["data"]["datasets"][0]["data"])) == 1


def test_infographic_long_label_keeps_value():
    """Срез метки не должен съедать значение — в этом был баг."""
    import urllib.parse
    facts = [{"label": "Производственные расходы" * 6, "value": "$200 млн"},
             {"label": "Сборы", "value": "$712 млн"}]
    url = build_url("T", facts)
    assert url
    config = json.loads(urllib.parse.unquote(url.split("c=")[1].split("&")[0]))
    assert any("$200 млн" in lbl for lbl in config["data"]["labels"])


def test_infographic_huge_facts_stay_within_url_limit():
    facts = [{"label": "О" * 300, "value": "Ч" * 300} for _ in range(5)]
    url = build_url("Т" * 200, facts)
    assert 0 < len(url) <= 2000


def test_infographic_url_length_under_2000():
    facts = [{"label": f"Факт {i}", "value": f"${i * 11} млн"} for i in range(1, 6)]
    assert len(build_url("Дюна: ключевые цифры", facts)) <= 2000


# --- траурные темы: ни опроса, ни загадки ------------------------------------

def test_sad_topics_restricted_to_safe_formats():
    weights = {fmt: 1 for fmt in
               ("poll", "riddle", "trailer", "video", "comparison", "interview")}
    picked = {_pick_format(weights, sad_topic=True) for _ in range(300)}
    assert picked <= {"news", "facts", "quote"}


def test_sad_topic_keeps_safe_format():
    assert _pick_format({"quote": 1}, sad_topic=True) == "quote"


# --- судья не затирает черновик пустыми коллекциями --------------------------

def test_judge_empty_containers_do_not_wipe_draft():
    cfg = SimpleNamespace(
        settings=Settings(), llm_base_url="http://llm.test", llm_api_key="k",
        judge_model="primary", judge_fallback_model="")
    draft = {"headline": "H", "text": "T",
             "poll_options": ["Дюна", "Оппенгеймер"],
             "image_candidates": ["Дюна", "Дюна 1984"],
             "infographic": {"title": "T", "facts": []}}
    empty_verdict = {"verdict": "accept", "poll_options": [], "image_candidates": [],
                     "infographic": {}, "headline": "", "text": ""}
    with patch("kino.judge._request_judge", return_value=(empty_verdict, "")):
        out = judge(_choice("poll"), dict(draft), cfg)
    assert out["poll_options"] == draft["poll_options"]
    assert out["image_candidates"] == draft["image_candidates"]
    assert out["infographic"] == draft["infographic"]


def test_judge_nonempty_containers_replace_draft():
    cfg = SimpleNamespace(
        settings=Settings(), llm_base_url="http://llm.test", llm_api_key="k",
        judge_model="primary", judge_fallback_model="")
    new_opts = ["Дюна", "Тернир"]
    with patch("kino.judge._request_judge",
               return_value=({"verdict": "accept", "poll_options": new_opts}, "")):
        out = judge(_choice("poll"),
                    {"headline": "H", "text": "T", "poll_options": ["A", "B"]}, cfg)
    assert out["poll_options"] == new_opts

# --- реестр маппинга: единый источник правды -------------------------------

def test_registry_maps_only_known_variations():
    assert set(_FORMAT_TO_KIND.values()) <= set(_VARIATIONS)


def test_registry_forced_formats_match_parametrized_list():
    """Реестр и список тестов должны совпадать: новый формат без теста виден сразу."""
    forced = {fmt for fmt, kind in _FORMAT_TO_KIND.items()}
    assert forced == {"video", "trailer", "infographic", "collage", "comparison",
                      "interview", "review", "opinion", "poll"}


def test_registry_kind_matches_pick_variation():
    for fmt, kind in _FORMAT_TO_KIND.items():
        picked = pick_variation(_pcfg(), {}, "", _choice(fmt))[0]
        if kind == "link":
            # link требует ссылку: без видео и ссылки будет фолбэк photo
            assert picked in ("link", "photo")
        else:
            assert picked == kind, f"{fmt}: ожидался {kind}, получен {picked}"


# --- постер YouTube как запасной кадр (find: _video_from_entry[1] терялся) ----

def _parse(xml: str):
    from kino.collect import _parse_feed
    from kino.config import Config, Settings, Source
    cfg = Config(settings=Settings())
    name, items, err = _parse_feed(Source(name="t", url="https://x/feed"), xml.encode(), cfg)
    assert err is None, err
    return items[0]


def test_youtube_link_gives_video_and_poster_image():
    item = _parse('<rss version="2.0"><channel><item><title>Trailer</title>'
                  '<link>https://d.example/1</link>'
                  '<description>Watch https://www.youtube.com/watch?v=dQw4w9TgXcU</description>'
                  '</item></channel></rss>')
    assert item.video_url == "https://www.youtube.com/watch?v=dQw4w9TgXcU"
    assert item.images == ["https://i.ytimg.com/vi/dQw4w9TgXcU/hqdefault.jpg"]


def test_feed_own_images_win_over_youtube_poster():
    xml = ('<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><item>'
           '<title>Trailer</title><link>https://d.example/1</link>'
           '<media:thumbnail url="https://cdn.example/first.jpg"/>'
           '<description>https://www.youtube.com/watch?v=dQw4w9TgXcU</description>'
           '</item></channel></rss>')
    item = _parse(xml)
    assert item.images[0] == "https://cdn.example/first.jpg"
    assert "https://i.ytimg.com/vi/dQw4w9TgXcU/hqdefault.jpg" in item.images


def test_iframe_embed_is_stripped_by_feedparser_not_matched():
    """feedparser вырезает iframe — постер берётся только из ссылки-текста."""
    item = _parse('<rss version="2.0"><channel><item><title>t</title>'
                  '<link>https://d.example/1</link>'
                  '<description><![CDATA[<iframe src="https://www.youtube.com/embed/dQw4w9TgXcU"></iframe>]]>'
                  '</description></item></channel></rss>')
    assert item.video_url == ""


# --- единственная точка обрезки подписи --------------------------------------

def test_text_variation_not_cut_by_photo_limit():
    """Регресс: текстовый пост резался редакторским лимитом фото (950)."""
    from kino.config import Config, Settings
    draft = {"headline": "Интервью", "text": "Т" * 3000, "question": "В?",
             "hashtags": ["кино"]}
    cfg = Config(settings=Settings())
    text_cap = build_caption(draft, "", cfg, kind="text")
    photo_cap = build_caption(draft, "", cfg, kind="photo")
    assert len(text_cap) > len(photo_cap)
    assert len(text_cap) <= 4096
    assert len(photo_cap) <= 1024


def test_photo_hard_limit_survives_raised_editorial_cap():
    """Если мягкий лимит поднимут выше потолка Telegram, пост не отобьётся."""
    from types import SimpleNamespace
    cfg = SimpleNamespace(settings=SimpleNamespace(caption_max_length=5000,
                                                  caption_hard_limit=5000))
    cap = build_caption({"headline": "H", "text": "Т" * 6000}, "", cfg, kind="photo")
    assert len(cap) <= 1024


def test_no_kind_keeps_editorial_limits():
    """Вызов без kind (демо, старые тесты) — прежнее поведение 950/1000."""
    from types import SimpleNamespace
    cfg = SimpleNamespace(settings=SimpleNamespace(caption_max_length=950,
                                                  caption_hard_limit=1000))
    cap = build_caption({"headline": "H", "text": "Т" * 3000}, "", cfg)
    assert 900 < len(cap) <= 1000
