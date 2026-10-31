"""Атомарное состояние между запусками."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # только для проверки типов: рантайм-импорт циклический
    from kino.schedule import DayState

logger = logging.getLogger(__name__)


@dataclass
class State:
    used_links: list[str] = field(default_factory=list)
    used_sigs: list[list[str]] = field(default_factory=list)
    # --- новое для 24/7 ---
    #: Локальная дата (YYYY-MM-DD) последней публикации и счётчик постов за день.
    #: Переживают рестарт serve: после падения процесс не должен запостить лишнее.
    day: str = ""
    posts_today: int = 0
    last_post_at: str = ""  # ISO локального времени


class StateLock:
    """Блокировка на файл, чтобы два запуска не пересеклись.

    Файл содержит PID владельца. Если процесс умер (SIGKILL, OOM, рестарт
    контейнера), файл остаётся — и без проверки PID пайплайн вставал навсегда:
    каждый следующий запуск получал «другой запуск уже выполняется». Поэтому
    мёртвый владелец перехватывается, а слишком старый файл (по mtime)
    считается брошенным.
    """

    #: Возраст файла, после которого блокировка считается брошенной (секунды).
    STALE_AFTER_SECONDS = 3600.0

    def __init__(self, path: Path, stale_after: float | None = None):
        self.path = path
        self._fd: int | None = None
        self.stale_after = self.STALE_AFTER_SECONDS if stale_after is None else stale_after

    def _read_owner(self) -> int | None:
        """PID владельца из файла. None, если файл пуст или испорчен."""
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Жив ли процесс с таким PID.

        os.kill(pid, 0) не посылает сигнал, только проверяет существование.
        PermissionError означает, что процесс есть, но чужой — тоже живой.
        """
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _is_stale(self) -> bool:
        """Брошена ли блокировка: владелец мёртв или файл слишком старый."""
        owner = self._read_owner()
        if owner is not None and not self._pid_alive(owner):
            logger.warning("lock %s: процесс %d мёртв, перехватываем", self.path.name, owner)
            return True
        if owner is None:
            # Пустой или испорченный файл — владельца нет.
            logger.warning("lock %s: не читается владелец, перехватываем", self.path.name)
            return True
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return False
        if age > self.stale_after:
            logger.warning(
                "lock %s: возраст %.0fс > %.0fс, считаем брошенной",
                self.path.name,
                age,
                self.stale_after,
            )
            return True
        return False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if attempt == 0 and self._is_stale():
                    self._remove_file()
                    continue
                return False
            os.write(self._fd, str(os.getpid()).encode())
            return True
        return False

    def _remove_file(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._remove_file()


def load_state(path: Path) -> State:
    if not path.exists():
        return State()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("state повреждён (%s), начинаем с пустого", exc)
        return State()
    links = data.get("used_links", [])
    sigs = data.get("used_sigs", [])
    if not isinstance(links, list):
        links = []
    if not isinstance(sigs, list):
        sigs = []
    # Новые поля дня читаются с дефолтами: старый state.json без них грузится
    # как «сегодня постов не было».
    day = data.get("day", "")
    posts_today = data.get("posts_today", 0)
    last_post_at = data.get("last_post_at", "")
    if not isinstance(day, str):
        day = ""
    if not isinstance(posts_today, int) or posts_today < 0:
        posts_today = 0
    if not isinstance(last_post_at, str):
        last_post_at = ""
    return State(
        used_links=[str(x) for x in links],
        used_sigs=[[str(y) for y in x] for x in sigs if isinstance(x, list)],
        day=day,
        posts_today=posts_today,
        last_post_at=last_post_at,
    )


def save_state(path: Path, state: State, max_links: int, max_sigs: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "used_links": state.used_links[-max_links:],
        "used_sigs": state.used_sigs[-max_sigs:],
        "day": state.day,
        "posts_today": state.posts_today,
        "last_post_at": state.last_post_at,
    }
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def record_choice(
    state: State,
    link: str,
    sig: list[str],
    max_links: int,
    max_sigs: int,
) -> None:
    if link not in state.used_links:
        state.used_links.append(link)
    state.used_links = state.used_links[-max_links:]
    state.used_sigs.append(sig)
    state.used_sigs = state.used_sigs[-max_sigs:]


def state_day(state: State) -> "DayState":
    """Достаёт DayState из State для планировщика.

    Мост между модулями: schedule оперирует своим DayState, а хранится он
    в State. Избегаем циклического импорта — DayState импортируем здесь,
    лениво, поэтому в аннотации он строкой (TYPE_CHECKING-импорт ниже).
    """
    from kino.schedule import DayState

    return DayState(
        date=state.day,
        posts=state.posts_today,
        last_post_at=state.last_post_at,
    )


def set_state_day(state: State, day: "DayState") -> None:
    """Записывает DayState из планировщика обратно в State."""
    state.day = day.date
    state.posts_today = day.posts
    state.last_post_at = day.last_post_at