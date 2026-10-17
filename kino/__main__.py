"""Точка входа: python -m kino run [--publish]."""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kino",
        description="Кино за 5 минут — генератор и публикатор постов о кино.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="собрать и обработать один пост")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="не публиковать и не сохранять state (по умолчанию)",
    )
    mode.add_argument(
        "--publish",
        action="store_true",
        help="реальная отправка в канал",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        publish = bool(args.publish)
        # Пайплайн подключается на следующих шагах.
        print(f"kino run: publish={publish}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())