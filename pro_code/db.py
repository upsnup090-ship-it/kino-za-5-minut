"""Единый доступ к SQLite: соединение на поток.

Зачем отдельный модуль. sqlite3 по умолчанию запрещает пользоваться
соединением из чужого потока (``check_same_thread=True``). Диспетчер
(``pro_code/dispatcher.py``) устроен так: объект БД создаётся в главном
потоке, а работает с ним каждый бот в своём потоке. Первое же обращение
падало с ``ProgrammingError: SQLite objects created in a thread can only be
used in that same thread`` — бот не отвечал вообще.

Решение идиоматичное для SQLite: одно соединение **на поток**, создаётся
лениво при первом обращении из этого потока. Никаких общих ``conn`` и
глобальных блокировок на весь модуль — параллельные потоки не мешают друг
другу, а WAL допускает одновременные чтения с записью.

``check_same_thread=False`` нужен, потому что соединение создаётся и
закрывается в разных вызовах одного и того же потока (например, при
``close()`` из-за остановки), и sqlite не должен перепроверять поток сам —
это уже сделано архитектурой «соединение на поток».
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Callable

#: Сколько ждать освобождения блокировки БД (мс), прежде чем упасть
#: ``database is locked``. Пять секунд — с запасом на тяжёлый запрос соседа.
BUSY_TIMEOUT_MS = 5000

#: Сериализует открытие соединений. Переключение в WAL требует
#: кратковременной эксклюзивной блокировки файла, и busy_timeout её не
#: покрывает: несколько потоков, открывающих одну базу разом (старт
#: диспетчера), получали ``database is locked``. Открытие — редкая
#: операция, поэтому общий lock здесь дешевле, чем ретраи вокруг прагмы.
_OPEN_LOCK = threading.Lock()


def ensure_dir(path: str | Path) -> None:
    """Создать каталог под файл БД. Вызывается перед открытием."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def connect(path: str | Path) -> sqlite3.Connection:
    """Открыть соединение с нужными прагмами.

    Ровно то, что повторялось в каждой БД проекта: Row-фабрика, WAL,
    busy_timeout. Разница в том, что теперь это одно место.

    Открытие идёт под ``_OPEN_LOCK``: PRAGMA journal_mode=WAL иначе
    ловил ``database is locked`` при параллельном старте потоков.
    """
    with _OPEN_LOCK:
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode=WAL")
    return conn


class ThreadedConn:
    """Соединение на поток с ленивым открытием.

    ``on_open`` — необязательный колбэк ``(conn) -> None``, который
    выполняется один раз для каждого нового соединения потока. Сюда БД
    вешает создание схемы: ``CREATE TABLE IF NOT EXISTS`` идемпотентен, а
    выполняется ровно один раз на поток.
    """

    def __init__(
        self,
        path: str | Path,
        on_open: Callable[[sqlite3.Connection], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self._on_open = on_open
        self._local = threading.local()
        #: Реестр всех открытых соединений — чтобы ``close_all()`` действительно
        #: закрыл их при остановке процесса, а не только соединение главного
        #: потока (иначе WAL-файлы останутся висеть).
        self._registry: list[sqlite3.Connection] = []
        self._lock = threading.Lock()

    @property
    def conn(self) -> sqlite3.Connection:
        """Соединение текущего потока. Открывается при первом обращении."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            ensure_dir(self.path)
            conn = connect(self.path)
            self._local.conn = conn
            with self._lock:
                self._registry.append(conn)
            if self._on_open is not None:
                self._on_open(conn)
        return conn

    def close(self) -> None:
        """Закрыть соединение текущего потока (если открыто)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        self._local.conn = None
        with self._lock:
            if conn in self._registry:
                self._registry.remove(conn)
        conn.close()

    def close_all(self) -> None:
        """Закрыть все соединения (остановка процесса)."""
        with self._lock:
            conns, self._registry = self._registry, []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()
