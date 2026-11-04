"""Отчёт по статистике форматов.

Читает logs/format_stats.csv и выводит топ форматов по частоте выбора.
Веса в config/formats.yaml НЕ трогает — корректировка вручную.

Запуск:
    venv/bin/python analysis/format_adjuster.py
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATS = ROOT / "logs" / "format_stats.csv"


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def report(rows: list[dict]) -> str:
    if not rows:
        return "нет данных: файл статистики пуст или отсутствует"

    format_counts = Counter(row.get("format", "?") for row in rows)
    source_counts = Counter(row.get("source", "?") for row in rows)
    total = len(rows)

    lines = [f"Всего записей: {total}", "", "Форматы:"]
    for fmt, count in format_counts.most_common():
        pct = count / total * 100
        lines.append(f"  {fmt:<10} {count:>4}  ({pct:5.1f}%)")

    lines.append("")
    lines.append("Источники:")
    for source, count in source_counts.most_common():
        lines.append(f"  {source:<20} {count:>4}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Отчёт по статистике форматов")
    parser.add_argument(
        "--stats",
        default=str(DEFAULT_STATS),
        help=f"путь к CSV (по умолчанию {DEFAULT_STATS})",
    )
    args = parser.parse_args(argv)

    rows = load_rows(Path(args.stats))
    print(report(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())