"""Тесты оффлайн-замера маршрутизации (Фаза 3, scripts/routing_report.py).

Замер обязан видеть ровно то, что видит бой: разбор фида — боевой
collect._parse_feed, выбор формата — та же арифметика pick_format
(веса ренормализуются внутри допущенных, нулевые веса выбрасываются,
пустой пул уходит в allowed[0]).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from kino.collect import FeedItem, _cache_path
from kino.config import Config, Settings, Source, load_config
from kino.routing import DEFAULT_ROUTING

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.routing_report import (  # noqa: E402
    build_report,
    collect_cache_items,
    format_text,
    main,
)

#: Все 16 форматов с боевыми весами (formats.yaml после Фазы 2).
FULL_WEIGHTS = {
    "facts": 8, "news": 21, "when": 16, "poll": 7, "quote": 6,
    "trailer": 5, "video": 5, "infographic": 4, "collage": 5,
    "behind_scenes": 5, "interview": 5, "announcement": 5,
    "review": 5, "comparison": 4, "riddle": 4, "opinion": 5,
}


def _cfg(weights: dict[str, int], routing: dict | None = None) -> Config:
    return Config(
        sources=[Source(name="Пресса", url="https://example.com/feed", kind="mixed")],
        weights=weights,
        routing=DEFAULT_ROUTING if routing is None else routing,
        settings=Settings(),
    )


def _item(title: str, snippet: str = "", link: str = "", video_url: str = "") -> FeedItem:
    return FeedItem(
        source="Пресса",
        title=title,
        link=link,
        pub_date="",
        ts=0.0,
        snippet=snippet,
        video_url=video_url,
    )


def _one(cfg: Config, item: FeedItem) -> dict:
    return build_report({"Пресса": [item]}, cfg)


# --- допуск форматов ---------------------------------------------------------


def test_flat_news_admits_news_collage_poll():
    """Плоская новость без признаков: news + collage + poll, ничего больше."""
    report = _one(_cfg(FULL_WEIGHTS), _item("Студия подтвердила продление сериала"))
    assert report["kinds"]["news"] == 1
    assert set(report["admit_rate_pct"]) == set(FULL_WEIGHTS)
    assert report["admit_rate_pct"]["news"] == 100.0
    assert report["admit_rate_pct"]["collage"] == 100.0
    assert report["admit_rate_pct"]["poll"] == 100.0
    assert report["admit_rate_pct"]["facts"] == 0.0
    assert report["admit_rate_pct"]["when"] == 0.0


def test_expected_share_sums_to_100():
    items = [
        _item("Студия подтвердила продление сериала"),
        _item("Премьера фильма назначена на 14 марта 2027 года"),
        _item("Интересные факты о классике жанра"),
        _item("Рецензия: стоит ли смотреть"),
    ]
    report = build_report({"Пресса": items}, _cfg(FULL_WEIGHTS))
    assert sum(report["expected_share_pct"].values()) == pytest.approx(100.0, abs=1.0)


def test_zero_weight_format_not_picked_but_first_allowed_fallback():
    """Формат с весом 0 не выбирается; пустой пул уходит в allowed[0] (как в бою)."""
    cfg = _cfg({"news": 10, "trailer": 0})
    item = _item("Трейлер фильма вышел", video_url="https://youtube.com/watch?v=1")
    report = _one(cfg, item)
    # trailer допущен, но вес 0 -> выбор уходит в news (первый допущенный)
    assert report["admit_rate_pct"]["trailer"] == 100.0
    assert report["expected_share_pct"]["news"] == 100.0
    assert report["expected_share_pct"].get("trailer", 0.0) == 0.0


# --- красные флаги шлюза (§10) -----------------------------------------------


def test_flag_facts_below_10_percent():
    """facts допущен реже 10% материалов — красный флаг."""
    items = [_item(f"Обычная новость номер {i}") for i in range(19)]
    items.append(_item("Интересные факты о классике жанра"))  # 1 из 20 = 5%
    report = build_report({"Пресса": items}, _cfg(FULL_WEIGHTS))
    assert any(flag.startswith("facts") for flag in report["flags"])


def test_flag_news_share_over_40():
    """Одни плоские новости -> news забирает более 40% выбора — красный флаг."""
    items = [_item(f"Обычная новость номер {i}") for i in range(10)]
    report = build_report({"Пресса": items}, _cfg(FULL_WEIGHTS))
    assert report["news_expected_share_pct"] > 40.0
    assert any("news" in flag for flag in report["flags"])


def test_flag_zero_admit_formats():
    """Формат из weights без единого допуска — красный флаг со списком."""
    report = build_report(
        {"Пресса": [_item("Обычная новость без признаков")]}, _cfg(FULL_WEIGHTS)
    )
    assert "trailer" in report["zero_admit_formats"]
    assert any("0 допусков" in flag for flag in report["flags"])


def test_no_flags_when_coverage_healthy():
    """Разнообразный набор с ужатым news — флагов нет."""
    weights = dict(FULL_WEIGHTS)
    weights["news"] = 5  # ужимаем news, чтобы его доля выбора не била порог 40%
    items = [
        _item("Студия подтвердила продление сериала"),
        _item("Премьера фильма назначена на 14 марта 2027 года"),
        _item("Интересные факты о классике жанра"),
        _item("Актёр дал интервью о съемках"),
        _item("Режиссёр сказал: «это фильм о вере и надежде»"),
        _item("Рецензия: стоит ли смотреть"),
        _item("Сборы фильма превысили 100 млн долларов"),
        _item("Трейлер фильма вышел", video_url="https://youtube.com/watch?v=1"),
        _item("Закулисье съемок нового блокбастера"),
    ]
    report = build_report({"Пресса": items}, _cfg(weights))
    assert report["flags"] == []
    assert report["zero_admit_formats"] == []


# --- разбор кэша --------------------------------------------------------------


def test_collect_cache_items_maps_sources_and_errors(tmp_path):
    """Slug-маппинг имён источников и понятные причины пропуска."""
    cfg = _cfg(FULL_WEIGHTS)
    cfg.sources = [
        Source(name="Reddit/movies", url="https://reddit.com/r/movies/.rss", kind="mixed"),
        Source(name="Отсутствующий", url="https://example.com/feed", kind="mixed"),
    ]
    cfg.cache_dir = tmp_path  # до записи файла — иначе попадём в реальный кэш
    rss = (
        "<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel>"
        "<title>t</title><item><title>Премьера назначена на 2027 год</title>"
        "<link>https://example.com/a</link><description>подробности</description></item>"
        "</channel></rss>"
    )
    _cache_path(cfg, "Reddit/movies").write_bytes(rss.encode("utf-8"))

    by_source, errors = collect_cache_items(cfg)
    assert "Reddit/movies" in by_source
    assert len(by_source["Reddit/movies"]) == 1
    assert errors == {"Отсутствующий": "нет в кэше"}


def test_integration_main_on_real_config(tmp_path, capsys):
    """main() на реальном конфиге репозитория с фикстурным кэшем из двух фидов."""
    env_file = tmp_path / "env.test"
    env_file.write_text(
        "BOT_TOKEN=123:test\n"
        "CHANNEL_CHAT_ID=-100\n"
        "ADMIN_CHAT_ID=1\n"
        "LLM_PROVIDER=apimart\n"
        "LLM_BASE_URL=https://api.apimart.ai/v1\n"
        "LLM_API_KEY=sk-apimart-test\n",
        encoding="utf-8",
    )
    cfg = load_config(str(env_file))
    cache = tmp_path / "cache"
    cache.mkdir()
    rss = (
        "<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel>"
        "<title>d</title><item><title>Рецензия на новый фильм</title>"
        "<link>https://example.com/r</link><description>вердикт критика</description></item>"
        "</channel></rss>"
    )
    deadline_name = _cache_path(cfg, "Deadline").name  # slug из боевого маппинга
    (cache / deadline_name).write_bytes(rss.encode("utf-8"))
    reddit_rss = rss.replace("Рецензия на новый фильм", "Сборы превысили 100 млн")
    (cache / "reddit_movies.xml").write_bytes(reddit_rss.encode("utf-8"))

    json_out = tmp_path / "report.json"
    code = main(
        ["--cache-dir", str(cache), "--env", str(env_file), "--json", str(json_out)]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Deadline" in out
    assert "reddit_movies" not in out  # в отчёте имя источника, не файла
    assert "нет в кэше" in out  # источники без кэша показаны как пропущенные
    data = json.loads(json_out.read_text(encoding="utf-8"))
    assert data["items"] == 2
    assert data["sources"]["Deadline"]["items"] == 1
    assert data["sources"]["Reddit/movies"]["kind"] == "mixed"
    assert format_text(data, cache)  # текстовый отчёт строится из JSON-структуры
