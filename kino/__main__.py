"""Точка входа: python -m kino run [--publish] | python -m kino draft "тема" [--dry-run]."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from kino.collect import FeedItem
from kino.choose import Choice
from kino.config import ConfigError, load_config
from kino.draft import DraftError, draft
from kino.horizon import HorizonError
from kino.pipeline import run
from kino.publish import notify_admin

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kino",
        description="Кино за 5 минут — генератор и публикатор постов о кино.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="собрать и обработать один пост")
    mode = run_parser.add_mutually_exclusive_group()
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

    draft_parser = sub.add_parser("draft", help="сгенерировать черновик по заданной теме")
    draft_parser.add_argument("topic", help="тема поста")
    draft_parser.add_argument(
        "--format",
        default="news",
        help="формат поста (по умолчанию news)",
    )
    draft_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="показать только тело запроса, без вызова LLM",
    )
    return parser


def _cmd_run(args) -> int:
    dry_run = not args.publish
    try:
        cfg = load_config()
        result = run(cfg, dry_run=dry_run)
        logger.info("формат: %s, dry_run: %s, published: %s", result.format, result.dry_run, result.published)
        logger.info("подпись (%d символов):\n%s", len(result.caption), result.caption)
        if result.image_url:
            logger.info("картинка: %s", result.image_url)
        return 0
    except ConfigError as exc:
        logger.error("ошибка конфигурации: %s", exc)
        return 1
    except HorizonError as exc:
        logger.error("ошибка источника Horizon: %s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001 — последний рубеж, сообщаем админу
        logger.exception("необработанная ошибка")
        try:
            cfg = load_config()
            notify_admin(cfg, f"необработанная ошибка: {exc.__class__.__name__}")
        except Exception:  # noqa: BLE001
            pass
        return 1


def _cmd_draft(args) -> int:
    try:
        cfg = load_config()
    except ConfigError as exc:
        logger.error("ошибка конфигурации: %s", exc)
        return 1

    item = FeedItem(
        source="cli",
        title=args.topic,
        link="",
        pub_date="",
        ts=0.0,
        snippet=args.topic,
    )
    choice = Choice(item=item, format=args.format, quality_score=0, sad_topic=False)

    if args.dry_run:
        from kino.draft import build_prompt

        body = build_prompt(choice, cfg)
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return 0

    try:
        data = draft(choice, cfg)
    except DraftError as exc:
        logger.error("черновик не сгенерирован: %s", exc)
        return 1

    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return _cmd_run(args)
    if args.command == "draft":
        return _cmd_draft(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())