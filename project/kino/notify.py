"""sd_notify: сообщения systemd из процесса без внешних зависимостей.

Нужен для `Type=notify` + `WatchdogSec` в `deploy/kino.service`. Без
watchdog падение процесса лечится `Restart=always`, а зависание — нет:
процесс, застрявший в сетевом вызове, висит живым и не публикует.

Протокол простой: systemd кладёт в окружение путь к unix-сокету
(`NOTIFY_SOCKET`), процесс пишет туда строки `KEY=value` через `\\n`.
Абстрактный сокет начинается с `@` — это ядерный namespace, а не путь в
файловой системе, и для него нужен нулевой байт в начале адреса.

Без `NOTIFY_SOCKET` (ручной запуск, тесты, cron) все вызовы — no-op.
Ошибки отправки не пробрасываются: сломанный watchdog не должен ронять
публикатор.
"""

from __future__ import annotations

import logging
import os
import socket

logger = logging.getLogger(__name__)

#: Путь к сокету уведомлений в окружении. Нет переменной — systemd не слушает.
NOTIFY_SOCKET_ENV = "NOTIFY_SOCKET"

#: Флаг «сообщение уже отправлено» — чтобы не писать одно и то же дважды
#: при рестартах внутри одного процесса.
_sent_ready = False


def is_available() -> bool:
    """Слушает ли нас systemd."""
    try:
        return bool(os.environ.get(NOTIFY_SOCKET_ENV))
    except ValueError:  # путь с нулевым байтом — негодное окружение
        return False


def _send(payload: str) -> bool:
    """Отправляет одну строку в сокет. True — ушло.

    Никогда не бросает: watchdog — вспомогательный механизм. `ValueError`
    ловится вместе с `OSError`: путь с нулевым байтом роняет уже чтение
    переменной окружения, до сокета дело не доходит.
    """
    try:
        address = os.environ.get(NOTIFY_SOCKET_ENV)
    except ValueError:
        return False
    if not address:
        return False

    sock: socket.socket | None = None
    try:
        # Абстрактный namespace: '@' в окружении = нулевой байт в адресе.
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        if address.startswith("@"):
            sock.connect("\0" + address[1:])
        else:
            sock.connect(address)
        sock.sendall(payload.encode("utf-8"))
        return True
    except (OSError, ValueError) as exc:
        logger.debug("sd_notify: не отправлено (%s)", exc)
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def ready(status: str = "") -> bool:
    """`READY=1` — процесс готов принимать работу. Для Type=notify.

    Отправляется один раз за жизнь процесса: повторный READY после
    рестарта внутри процесса systemd не ждёт, а лишний пакет только шумит.
    """
    global _sent_ready
    if _sent_ready:
        return False
    line = "READY=1"
    if status:
        line += f"\nSTATUS={status}"
    ok = _send(line)
    if ok:
        _sent_ready = True
    return ok


def watchdog() -> bool:
    """`WATCHDOG=1` — «я жив». Каждый тик цикла.

    Не пришёл за `WatchdogSec` — systemd перезапускает процесс.
    """
    return _send("WATCHDOG=1")


def status(text: str) -> bool:
    """Строка состояния для `systemctl status`. Однострочная."""
    return _send(f"STATUS={text.replace(chr(10), ' ')[:200]}")


def stopping(text: str = "остановка") -> bool:
    """`STOPPING=1` — начинаем мягкую остановку."""
    return _send(f"STOPPING=1\nSTATUS={text}")


def reset_for_tests() -> None:
    """Сбрасывает флаг READY. Только для тестов."""
    global _sent_ready
    _sent_ready = False
