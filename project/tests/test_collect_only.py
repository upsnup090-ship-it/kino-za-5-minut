"""`run --collect-only`: сбор фидов без LLM и без публикации.

Нужен для замера маршрутизации (Фаза 6): `routing_report.py` читает
`data/feed_cache/*.xml`, а боевой `run` пишет туда же. Полный `run --dry-run`
тоже наполнил бы кэш, но потратил бы токены на черновик и судью, которые
тут же выбрасываются.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kino.__main__ import _cmd_collect_only, build_parser


def _cfg(tmp_path: Path) -> SimpleNamespace:
    cache = tmp_path / "feed_cache"
    cache.mkdir()
    return SimpleNamespace(cache_dir=cache, settings=SimpleNamespace(top_n=25))


def _items(n: int) -> list[SimpleNamespace]:
    return [SimpleNamespace(link=f"https://x/{i}") for i in range(n)]


def test_parser_has_collect_only_flag():
    args = build_parser().parse_args(["run", "--collect-only"])
    assert args.collect_only is True


def test_collect_only_default_false():
    args = build_parser().parse_args(["run"])
    assert args.collect_only is False


def test_collect_only_no_llm_called(tmp_path):
    """Ключевое: ни draft, ни judge не вызываются — токены не тратятся."""
    cfg = _cfg(tmp_path)
    collected = SimpleNamespace(items=_items(3), fresh_count=3, broken=[])

    with patch("kino.collect.collect", return_value=collected), \
         patch("kino.pipeline.draft") as draft, \
         patch("kino.pipeline.judge_with_analysis") as judge:
        code = _cmd_collect_only(cfg)

    assert code == 0
    draft.assert_not_called()
    judge.assert_not_called()


def test_collect_only_returns_one_on_empty_collect(tmp_path):
    cfg = _cfg(tmp_path)
    collected = SimpleNamespace(items=[], fresh_count=0, broken=["feed: timeout"])

    with patch("kino.collect.collect", return_value=collected):
        code = _cmd_collect_only(cfg)

    assert code == 1


def test_collect_only_reports_broken_sources(tmp_path, caplog):
    import logging

    cfg = _cfg(tmp_path)
    collected = SimpleNamespace(
        items=_items(1), fresh_count=1, broken=["a: 429", "b: parse error"],
    )

    with caplog.at_level(logging.WARNING, logger="kino.__main__"), \
         patch("kino.collect.collect", return_value=collected):
        _cmd_collect_only(cfg)

    warnings = [r for r in caplog.records if "битый источник" in r.getMessage()]
    assert len(warnings) == 2


def test_collect_only_counts_cache_files(tmp_path, caplog):
    import logging

    cfg = _cfg(tmp_path)
    (cfg.cache_dir / "a.xml").write_text("<rss/>", encoding="utf-8")
    (cfg.cache_dir / "b.xml").write_text("<rss/>", encoding="utf-8")
    collected = SimpleNamespace(items=_items(2), fresh_count=2, broken=[])

    with caplog.at_level(logging.INFO, logger="kino.__main__"), \
         patch("kino.collect.collect", return_value=collected):
        _cmd_collect_only(cfg)

    assert any("кэш: 2 файлов" in r.getMessage() for r in caplog.records)


def test_summary_reports_fresh_not_truncated_pool(tmp_path, caplog):
    """«Собрано» — это fresh_count, а не len(items): items обрезан до top_n.

    На сервере 1.11 лог печатал «собрано: 25, свежих 600» при 770 материалах
    в кэше — пул для пайплайна (top_n=25) выдавался за объём сбора.
    """
    import logging

    cfg = _cfg(tmp_path)
    cfg.settings = SimpleNamespace(top_n=25)
    collected = SimpleNamespace(items=_items(25), fresh_count=600, broken=[])

    with caplog.at_level(logging.INFO, logger="kino.__main__"), \
         patch("kino.collect.collect", return_value=collected):
        _cmd_collect_only(cfg)

    line = next(r.getMessage() for r in caplog.records if "собрано:" in r.getMessage())
    assert "600 свежих" in line
    assert "25" in line  # пул, но подписан как пул, а не как «собрано»
    assert "top_n=25" in line