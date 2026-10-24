"""Оффлайн-замер маршрутизации форматов (Фаза 3, docs/source-routing-design.md).

Без LLM и без сети. Читает кэш фидов (data/feed_cache/*.xml — бой пишет туда
каждый удачный ответ), разбирает его тем же кодом, что и бой
(kino.collect._parse_feed), прогоняет routing по каждому материалу и печатает:

* распределение классов материала (11 классов, §3 дока) и сигналов;
* покрытие форматов — доля материалов, где формат допущен;
* ожидаемую долю выбора формата — среднее по материалам от взвешенного
  распределения внутри допущенных (ровно то, что pick_format выберет в бою
  на длинной дистанции);
* долю news (фолбэка) в ожидаемом выборе и красные флаги шлюза (§10):
    - facts допущен менее чем в 10% материалов;
    - news забирает более 40% выбора;
    - форматы с 0 допусков на всём наборе.

Замер по кэшу — снимок на дату файлов кэша. Для шлюза перед Фазой 4
перезапустить на сервере со свежими фидами: python scripts/routing_report.py

Запуск:
    venv/bin/python scripts/routing_report.py
    venv/bin/python scripts/routing_report.py --json report.json
    venv/bin/python scripts/routing_report.py --cache-dir /tmp/cache --env /path/.env
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kino.collect import FeedItem, _cache_path, _parse_feed  # noqa: E402
from kino.config import Config, load_config  # noqa: E402
from kino.routing import (  # noqa: E402
    KINDS,
    SIGNALS,
    allowed_formats,
    detect_kinds,
    detect_signals,
)

NEWS = "news"
FACTS = "facts"
FACTS_MIN_ADMIT = 10.0  # % материалов, ниже — красный флаг (§10)
NEWS_MAX_SHARE = 40.0  # % выбора news, выше — красный флаг (§10)


def collect_cache_items(
    cfg: Config, cache_dir: Path | None = None
) -> tuple[dict[str, list[FeedItem]], dict[str, str]]:
    """Разбирает кэш фидов боевым парсером.

    Возврат: ({имя источника: items}, {имя источника: причина пропуска}).
    Источник без файла в кэше — пропуск с причиной, не ошибка: замер должен
    показать и дыры в кэше (например, YT-фиды, живые только с сервера).
    """
    if cache_dir is not None:
        cfg.cache_dir = Path(cache_dir)
    by_source: dict[str, list[FeedItem]] = {}
    errors: dict[str, str] = {}
    for source in cfg.sources:
        path = _cache_path(cfg, source.name)
        try:
            payload = path.read_bytes()
        except OSError:
            errors[source.name] = "нет в кэше"
            continue
        if not payload:
            errors[source.name] = "пустой файл кэша"
            continue
        _name, items, err = _parse_feed(source, payload, cfg)
        if items is None:
            errors[source.name] = err or "ошибка разбора"
            continue
        by_source[source.name] = items
    return by_source, errors


def build_report(
    by_source: dict[str, list[FeedItem]],
    cfg: Config,
    source_errors: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Счёт по материалам. Чистая функция — вся статистика отсюда, без I/O.

    Ожидаемая доля формата = среднее по материалам его взвешенной доли среди
    допущенных: сумма по всем форматам сходится к 100%, как выбор pick_format
    на длинной дистанции. Если ни один допущенный не имеет веса, весь выбор
    уходит в allowed[0] — как в pick_format.
    """
    routing = cfg.routing or {}
    enabled = bool(routing.get("enabled", True))
    fallback = str(routing.get("fallback", NEWS))
    weights = {fmt: float(w) for fmt, w in cfg.weights.items()}

    kind_counter: Counter[str] = Counter()
    signal_counter: Counter[str] = Counter()
    admit_counter: Counter[str] = Counter()
    share_sum: dict[str, float] = {fmt: 0.0 for fmt in weights}
    news_share = 0.0
    total = 0
    per_source: dict[str, dict[str, Any]] = {}

    for name, items in sorted(by_source.items()):
        source = next(s for s in cfg.sources if s.name == name)
        src_kinds: Counter[str] = Counter()
        for item in items:
            total += 1
            kinds = detect_kinds(item, source.kind)
            signals = detect_signals(item)
            allowed = allowed_formats(kinds, signals, routing)
            for kind in kinds:
                kind_counter[kind] += 1
                src_kinds[kind] += 1
            for signal in signals:
                signal_counter[signal] += 1
            for fmt in allowed:
                admit_counter[fmt] += 1
            pool = {f: weights.get(f, 0.0) for f in allowed}
            pool = {f: w for f, w in pool.items() if w > 0}
            if pool:
                pool_total = sum(pool.values())
                for fmt, w in pool.items():
                    share = w / pool_total
                    share_sum[fmt] = share_sum.get(fmt, 0.0) + share
                    if fmt == NEWS:
                        news_share += share
            else:
                first = allowed[0]
                share_sum[first] = share_sum.get(first, 0.0) + 1.0
                if first == NEWS:
                    news_share += 1.0
        per_source[name] = {
            "kind": source.kind,
            "items": len(items),
            "kinds": dict(src_kinds.most_common()),
        }

    admit_rate = {fmt: 100.0 * admit_counter.get(fmt, 0) / total for fmt in weights if total}
    # Только форматы из weights: формат вне weights в бою имеет вес 0 и не выбирается.
    expected_share = {
        fmt: 100.0 * share_sum.get(fmt, 0.0) / total for fmt in weights if total
    }
    news_expected_share = 100.0 * news_share / total if total else 0.0
    zero_admits = sorted(fmt for fmt in weights if admit_counter.get(fmt, 0) == 0)

    flags: list[str] = []
    if total:
        # Порог §10 про facts с весом: если facts вообще не в weights — это выбор
        # конфига, а не находка маршрутизации.
        if FACTS in weights and admit_rate.get(FACTS, 0.0) < FACTS_MIN_ADMIT:
            flags.append(
                f"facts допущен в {admit_rate.get(FACTS, 0.0):.1f}% материалов "
                f"(<{FACTS_MIN_ADMIT:.0f}%)"
            )
        if news_expected_share > NEWS_MAX_SHARE:
            flags.append(
                f"news (фолбэк) забирает {news_expected_share:.1f}% выбора "
                f"(>{NEWS_MAX_SHARE:.0f}%)"
            )
        if zero_admits:
            flags.append("0 допусков на всём наборе: " + ", ".join(zero_admits))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "routing_enabled": enabled,
        "fallback": fallback,
        "items": total,
        "sources": per_source,
        "source_errors": source_errors or {},
        "kinds": {kind: kind_counter.get(kind, 0) for kind in KINDS},
        "signals": {signal: signal_counter.get(signal, 0) for signal in SIGNALS},
        "admit_rate_pct": {fmt: round(v, 1) for fmt, v in admit_rate.items()},
        "expected_share_pct": {fmt: round(v, 1) for fmt, v in expected_share.items()},
        "news_expected_share_pct": round(news_expected_share, 1),
        "zero_admit_formats": zero_admits,
        "flags": flags,
    }


def format_text(report: dict[str, Any], cache_dir: Path) -> str:
    """Человекочитаемый отчёт."""
    lines: list[str] = []
    state = "включена" if report["routing_enabled"] else "ВЫКЛЮЧЕНА (routing.enabled: false)"
    lines.append(f"Замер маршрутизации — оффлайн по кэшу {cache_dir}")
    lines.append(f"Маршрутизация: {state}; фолбэк: {report['fallback']}")
    lines.append(
        f"Материалов: {report['items']} из {len(report['sources'])} источников "
        f"с кэшем ({len(report['source_errors'])} источников пропущено)"
    )
    if report["source_errors"]:
        lines.append("Пропущены (нет в кэше / ошибка разбора):")
        for name, reason in sorted(report["source_errors"].items()):
            lines.append(f"  {name}: {reason}")
    lines.append("")
    lines.append("По источникам:")
    for name, info in report["sources"].items():
        top = ", ".join(f"{k}:{n}" for k, n in list(info["kinds"].items())[:3])
        lines.append(f"  {name:<22} {info['kind']:<7} {info['items']:>4} шт   {top}")
    lines.append("")
    lines.append("Классы материала (доля от всех материалов, классы пересекаются):")
    total = report["items"] or 1
    for kind, count in report["kinds"].items():
        if count:
            lines.append(f"  {kind:<14} {100.0 * count / total:5.1f}%  ({count})")
    lines.append("")
    lines.append("Сигналы: " + "  ".join(
        f"{signal} {100.0 * count / total:.1f}%"
        for signal, count in report["signals"].items()
        if count
    ))
    lines.append("")
    lines.append("Покрытие форматов (допущен / ожидаемая доля выбора):")
    for fmt, rate in report["admit_rate_pct"].items():
        share = report["expected_share_pct"].get(fmt, 0.0)
        mark = "  <-- 0 допусков" if fmt in report["zero_admit_formats"] else ""
        lines.append(f"  {fmt:<14} {rate:5.1f}%   {share:5.1f}%{mark}")
    lines.append("")
    lines.append(f"Фолбэк news: ожидаемая доля выбора {report['news_expected_share_pct']:.1f}%")
    lines.append("")
    if report["flags"]:
        lines.append("КРАСНЫЕ ФЛАГИ шлюза (§10):")
        for flag in report["flags"]:
            lines.append(f"  - {flag}")
    else:
        lines.append("Красных флагов нет — шлюз §10 по этим метрикам пройден.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache-dir", help="каталог кэша фидов (по умолчанию из конфига)")
    parser.add_argument("--json", dest="json_path", help="дополнительно сохранить отчёт в JSON")
    parser.add_argument("--env", help="путь к env-файлу для load_config")
    args = parser.parse_args(argv)

    cfg = load_config(args.env) if args.env else load_config()
    by_source, errors = collect_cache_items(cfg, Path(args.cache_dir) if args.cache_dir else None)
    report = build_report(by_source, cfg, errors)

    cache_dir = cfg.cache_dir
    print(format_text(report, cache_dir))
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nJSON: {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
