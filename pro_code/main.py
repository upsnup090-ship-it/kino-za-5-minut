#!/usr/bin/env python3
"""pro_code — точка входа диспетчера ботов.

Запуск:
  python -m pro_code.main

Загружает .env → конфиг → проверяет токены → запускает polling-потоки.
"""

from __future__ import annotations

import logging
import sys

from pro_code.config import ConfigError, load_config
from pro_code.dispatcher import run_dispatcher


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> int:
    setup_logging()
    logger = logging.getLogger(__name__)

    try:
        cfg = load_config()
    except ConfigError as exc:
        logger.error("ошибка конфигурации: %s", exc)
        return 1

    logger.info(
        "конфиг загружен: admin=%s, клиентов=%d",
        cfg.admin_chat_id, len(cfg.clients),
    )

    run_dispatcher(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
