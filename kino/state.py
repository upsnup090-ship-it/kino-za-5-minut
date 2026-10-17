"""Атомарное состояние между запусками."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class State:
    used_links: list[str] = field(default_factory=list)
    used_sigs: list[list[str]] = field(default_factory=list)


class StateLock:
    """Блокировка на файл, чтобы два запуска не пересеклись."""

    def __init__(self, path: Path):
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self._fd, str(os.getpid()).encode())
            return True
        except FileExistsError:
            return False

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


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
    return State(
        used_links=[str(x) for x in links],
        used_sigs=[[str(y) for y in x] for x in sigs if isinstance(x, list)],
    )


def save_state(path: Path, state: State, max_links: int, max_sigs: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "used_links": state.used_links[-max_links:],
        "used_sigs": state.used_sigs[-max_sigs:],
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