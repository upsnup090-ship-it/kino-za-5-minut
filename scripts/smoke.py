#!/usr/bin/env python3
"""Смок-проверка перед запуском диспетчера. Ничего не публикует.

Запуск:

    cd /opt/kino/project && venv/bin/python scripts/smoke.py

Что проверяет по шагам:

1. ``.env`` читается, обязательные ключи заданы (значения не печатаются).
2. ``load_config()`` собирает клиентов и мастер-бота.
3. ``TOKEN_ENCRYPTION_KEY`` рабочий (шифр создаётся).
4. ``getMe`` каждого токена — живой ли бот (сеть: api.telegram.org).
5. ``import kino`` (пакет автопостинга) доступен.
6. Все БД открываются и схема создаётся.
7. Права на ``.env`` (не должен быть читаем всем).

Код возврата 0 — всё в порядке, 1 — есть проваленные шаги.
Секреты не печатаются ни на одном шаге (§4.7).
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Результаты шагов: (имя, ok, пояснение).
_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    from pro_code.bootstrap import ensure_kino_importable

    # --- 1. .env -----------------------------------------------------------
    from dotenv import dotenv_values

    env_path = ROOT / ".env"
    if not check(".env существует", env_path.is_file(), str(env_path)):
        _summary()
        return 1

    values = dotenv_values(env_path)
    for key in ("ADMIN_CHAT_ID", "MASTER_BOT_TOKEN", "TOKEN_ENCRYPTION_KEY"):
        check(f"{key} задан", bool((values.get(key) or "").strip()))

    # --- 7. права на .env (проверяем сразу: секрет) ------------------------
    mode = stat.S_IMODE(env_path.stat().st_mode)
    check(
        ".env не читаем всем (chmod 600)",
        not (mode & (stat.S_IRGRP | stat.S_IROTH)),
        f"права {oct(mode)}",
    )

    # --- 2. конфиг ---------------------------------------------------------
    try:
        from pro_code.config import ConfigError, load_config

        cfg = load_config()
        check(
            "конфиг собирается",
            True,
            f"admin задан, клиентов {len(cfg.clients)}, "
            f"мастер-бот {'есть' if cfg.master_token else 'нет'}",
        )
    except ConfigError as exc:
        check("конфиг собирается", False, str(exc))
        _summary()
        return 1

    # --- 3. шифр -----------------------------------------------------------
    try:
        from pro_code.crypto import TokenCipher

        TokenCipher()
        check("ключ шифрования рабочий", True)
    except Exception as exc:  # noqa: BLE001
        check("ключ шифрования рабочий", False, str(exc))

    # --- 4. getMe по каждому токену ---------------------------------------
    from pro_code.tg_api import TgError, get_me

    tokens: list[tuple[str, str]] = []
    if cfg.master_token:
        tokens.append(("мастер-бот", cfg.master_token))
    for client in cfg.clients:
        tokens.append((f"клиент {client.user_id}", client.token))

    if not tokens:
        check("есть хотя бы один токен", False, "ни мастер-бота, ни клиентов")
    for label, token in tokens:
        try:
            info = get_me(token)
            check(
                f"getMe: {label}",
                True,
                f"@{info.get('username')} (id {info.get('id')})",
            )
        except TgError as exc:
            # Текст ошибки уже очищен от токена (pro_code/sanitize.py).
            check(f"getMe: {label}", False, str(exc))

    # --- 5. Kino -----------------------------------------------------------
    ensure_kino_importable()
    try:
        import kino  # noqa: F401

        check("пакет kino импортируется", True, str(Path(kino.__file__).parent))
    except Exception as exc:  # noqa: BLE001
        check("пакет kino импортируется", False, str(exc))

    # --- 6. БД -------------------------------------------------------------
    try:
        from pro_code.clients_db import ClientsDb
        from pro_code.kino_auto_db import KinoAutoDb
        from project_ruch.ruch_db import RuchDb
        from project_ruch.rezerv_post.rezerv_db import ReserveDb

        for name, factory in (
            ("clients.db", ClientsDb),
            ("kino_auto.db", KinoAutoDb),
            ("ruch.db", RuchDb),
            ("rezerv.db", ReserveDb),
        ):
            db = factory()
            db.close_all()
            check(f"БД {name} открывается", True)
    except Exception as exc:  # noqa: BLE001
        check("БД открываются", False, str(exc))

    # Директория данных на запись (бой пишет туда).
    data_dir = ROOT / "data"
    check("каталог data/ доступен на запись", os.access(data_dir, os.W_OK), str(data_dir))

    return _summary()


def _summary() -> int:
    failed = [name for name, ok, _ in _results if not ok]
    print()
    if failed:
        print(f"ИТОГ: {len(failed)} провал(ов): " + ", ".join(failed))
        return 1
    print(f"ИТОГ: все {len(_results)} проверок пройдены. Можно запускать диспетчер.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
