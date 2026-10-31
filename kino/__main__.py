"""Точка входа: python -m kino run [--publish] | python -m kino draft "тема" [--dry-run]."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from kino.collect import FeedItem
from kino.choose import Choice
from kino.config import Config, ConfigError, load_config
from kino.draft import DraftError, draft
from kino.horizon import HorizonError
from kino.pipeline import run
from kino.publish import notify_admin

ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger(__name__)


def setup_logging(cfg: Config | None = None, *, force_console: bool = False) -> None:
    """Настраивает логи: файл с ротацией + stdout.

    Если передан cfg — берём настройки из его секции logging. Иначе (ручной
    запуск без конфига) — только stdout на INFO. force_console подавляет файл
    (нужно для тестов и отладки, где файл мешает).
    """
    if cfg is None or force_console:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        return

    from kino.logging_util import setup_logging as _setup

    _setup(
        file=ROOT / cfg.settings.log_file,
        level=cfg.settings.log_level,
        max_bytes=cfg.settings.log_max_bytes,
        backup_count=cfg.settings.log_backup_count,
        console=cfg.settings.log_console,
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

    serve_parser = sub.add_parser("serve", help="работать 24/7: публиковать по расписанию")
    serve_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="не публиковать, но проходить весь цикл",
    )
    serve_parser.add_argument(
        "--once",
        action="store_true",
        help="один проход цикла и выход (для отладки)",
    )
    serve_parser.add_argument(
        "--tick-seconds",
        type=int,
        default=None,
        help="как часто проверять расписание (секунды), иначе из конфига",
    )

    sched_parser = sub.add_parser(
        "schedule", help="расписание по слотам: seed, list, add, del, on, off, clear, runs"
    )
    sched_sub = sched_parser.add_subparsers(dest="schedule_action", required=True)

    sched_sub.add_parser("seed", help="заполнить сетку владельца (13 слотов)")
    sched_sub.add_parser("list", help="показать слоты по дням")

    add_parser = sched_sub.add_parser("add", help="добавить слот")
    add_parser.add_argument("weekday", help="день: 0-6 или пн..вс")
    add_parser.add_argument("time", help="время HH:MM")

    del_parser = sched_sub.add_parser("del", help="удалить слот")
    del_parser.add_argument("weekday", help="день: 0-6 или пн..вс")
    del_parser.add_argument("time", help="время HH:MM")

    on_parser = sched_sub.add_parser("on", help="включить слот")
    on_parser.add_argument("weekday", help="день: 0-6 или пн..вс")
    on_parser.add_argument("time", help="время HH:MM")

    off_parser = sched_sub.add_parser("off", help="выключить слот")
    off_parser.add_argument("weekday", help="день: 0-6 или пн..вс")
    off_parser.add_argument("time", help="время HH:MM")

    clear_parser = sched_sub.add_parser("clear", help="убрать все слоты дня")
    clear_parser.add_argument("weekday", help="день: 0-6 или пн..вс")

    runs_parser = sched_sub.add_parser("runs", help="последние записи журнала прогонов")
    runs_parser.add_argument("--limit", type=int, default=20, help="сколько записей (20)")
    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    dry_run = not args.publish
    try:
        cfg = load_config()
        setup_logging(cfg)
        result = run(cfg, dry_run=dry_run)
        logger.info("формат: %s, dry_run: %s, published: %s", result.format, result.dry_run, result.published)
        if result.kind:
            logger.info("вариация: %s (выбрана %s)", result.kind, result.requested_kind or result.kind)
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
    except DraftError as exc:
        # Ожидаемая ошибка генерации (модель, лимиты, сеть): без трейсбека,
        # но с уведомлением админу — иначе прогон под cron падает молча.
        logger.error("черновик не сгенерирован: %s", exc)
        try:
            cfg = load_config()
            notify_admin(cfg, f"черновик не сгенерирован: {exc}")
        except Exception:  # noqa: BLE001
            pass
        return 1
    except Exception as exc:  # noqa: BLE001 — последний рубеж, сообщаем админу
        logger.exception("необработанная ошибка")
        try:
            cfg = load_config()
            notify_admin(cfg, f"необработанная ошибка: {exc.__class__.__name__}")
        except Exception:  # noqa: BLE001
            pass
        return 1


def _cmd_draft(args: argparse.Namespace) -> int:
    try:
        cfg = load_config()
        setup_logging(cfg)
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


def _cmd_serve(args: argparse.Namespace) -> int:
    """serve: цикл 24/7. Ошибка конфига = выход без цикла."""
    try:
        cfg = load_config()
    except ConfigError as exc:
        logger.error("ошибка конфигурации: %s", exc)
        return 1
    setup_logging(cfg)

    from kino.serve import serve

    return serve(
        cfg,
        dry_run=args.dry_run,
        once=args.once,
        tick_seconds=args.tick_seconds,
    )


def _cmd_schedule(args: argparse.Namespace) -> int:
    """schedule: управление слотами в data/kino.db.

    Команды быстрые и не запускают пайплайн: seed/list/add/del/on/off/clear/runs.
    Путь к БД — cfg.db_path (env KINO_DB_PATH перекрывает settings.yaml), чтобы
    тесты и отладка не писали в боевой файл.
    """
    from kino.schedule_db import ScheduleDb, ScheduleDbError, format_week

    try:
        cfg = load_config()
    except ConfigError as exc:
        logger.error("ошибка конфигурации: %s", exc)
        return 1

    db = ScheduleDb(cfg.db_path)
    action = args.schedule_action

    try:
        if action == "seed":
            added = db.seed_defaults()
            total = db.count_slots()
            print(f"seed: добавлено {added}, всего слотов {total}")
            print(format_week(db.list_slots()))
            return 0

        if action == "list":
            slots = db.list_slots()
            if not slots:
                print("слотов нет. Запусти: python -m kino schedule seed")
                return 0
            print(f"слотов {len(slots)} (включено {sum(1 for s in slots if s.enabled)}):")
            print(format_week(slots))
            return 0

        if action == "add":
            slot = db.add_slot(args.weekday, args.time)
            print(f"добавлен слот: {slot}")
            return 0

        if action == "del":
            if db.delete_slot(args.weekday, args.time):
                print("слот удалён")
                return 0
            print("такого слота нет")
            return 1

        if action in ("on", "off"):
            slot = db.set_enabled(args.weekday, args.time, action == "on")
            print(f"слот {slot.weekday_name} {slot.time}: {'включён' if slot.enabled else 'выключен'}")
            return 0

        if action == "clear":
            count = db.clear_weekday(args.weekday)
            print(f"удалено слотов: {count}")
            return 0

        if action == "runs":
            records = db.recent_runs(limit=args.limit)
            if not records:
                print("журнал прогонов пуст")
                return 0
            for rec in records:
                stamp = rec.started_at[:16].replace("T", " ")
                tail = f" — {rec.reason}" if rec.reason else ""
                print(f"{stamp}  {rec.outcome:<9}  {rec.slot_at[:16]}{tail}")
            return 0
    except ScheduleDbError as exc:
        logger.error("расписание: %s", exc)
        return 1

    print(f"неизвестное действие schedule: {action}")
    return 1


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return _cmd_run(args)
    if args.command == "draft":
        return _cmd_draft(args)
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "schedule":
        return _cmd_schedule(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())