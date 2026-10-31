"""Цикл 24/7: связывает расписание, дедлайн и логи в один процесс.

serve не держит lock постоянно — lock берётся внутри run() на время одного
поста и отпускается. Иначе ручной `run --publish` не смог бы запуститься,
пока работает serve.

Остановка: SIGTERM/SIGINT ставят флаг stopping. Текущий пост доигрывается
(публикация не обрывается на середине), потом цикл выходит.
"""

from __future__ import annotations

import logging
import signal
import time
from datetime import datetime
from pathlib import Path

from kino.config import Config
from kino.logging_util import purge_old_logs
from kino.pipeline import run
from kino.schedule import (
    DayState,
    ScheduleConfig,
    apply_post,
    describe_day,
    next_slot,
    parse_windows,
    should_publish,
)
from kino.state import load_state, save_state, set_state_day, state_day

logger = logging.getLogger(__name__)


class Stopper:
    """Флаг остановки, управляемый сигналами."""

    def __init__(self) -> None:
        self._stop = False

    def request(self, *args: object) -> None:  # сигнатура signal handler
        self._stop = True
        logger.info("получен сигнал остановки, завершаю после текущего шага")

    @property
    def stopping(self) -> bool:
        return self._stop

    def install(self) -> None:
        signal.signal(signal.SIGTERM, self.request)
        signal.signal(signal.SIGINT, self.request)


def build_schedule(cfg: Config) -> ScheduleConfig:
    """Собирает расписание из конфига (уже провалидирован при загрузке)."""
    return ScheduleConfig(
        windows=parse_windows(cfg.settings.schedule_windows),
        max_posts_per_day=cfg.settings.max_posts_per_day,
        min_gap_minutes=cfg.settings.min_gap_minutes,
        tz=cfg.settings.timezone,
    )


def _persist_day_counter(
    cfg: Config, schedule: ScheduleConfig, day: DayState, now: datetime
) -> None:
    """Сохраняет счётчик дня в state.

    run() уже сохранил used_links; перезагружаем его и дописываем поля дня,
    чтобы не потерять ни то, ни другое. Ошибки записи не роняют цикл.
    """
    try:
        state = load_state(cfg.state_path)
        set_state_day(state, day)
        save_state(
            cfg.state_path,
            state,
            cfg.settings.state_max_links,
            cfg.settings.state_max_signatures,
        )
    except OSError as exc:
        logger.warning("не удалось сохранить счётчик дня: %s", exc)


def _maybe_purge(cfg: Config, last_purge: float | None, now: float) -> float | None:
    """Раз в purge_interval_hours чистит старые логи. Возвращает время последней чистки."""
    interval = cfg.settings.log_purge_interval_hours * 3600
    if last_purge is not None and (now - last_purge) < interval:
        return last_purge
    try:
        log_dir = _log_path(cfg).parent
        purge_old_logs(
            log_dir,
            retention_days=cfg.settings.log_retention_days,
            max_total_bytes=cfg.settings.log_max_total_bytes,
        )
    except Exception as exc:  # noqa: BLE001 — чистка не должна ронять цикл
        logger.warning("автоочистка логов не сработала: %s", exc)
    return now


def _log_path(cfg: Config) -> Path:
    return Path(cfg.settings.log_file)


def serve(cfg: Config, *, dry_run: bool = False, once: bool = False, tick_seconds: int | None = None) -> int:
    """Основной цикл. Возвращает код выхода (0 — нормально).

    tick_seconds: как часто проверять расписание; None — из конфига.
    once: один проход и выход (для отладки).
    """
    schedule = build_schedule(cfg)
    tick = tick_seconds or cfg.settings.tick_seconds

    stopper = Stopper()
    stopper.install()

    # Автоочистка логов при старте, до первого поста.
    last_purge = _maybe_purge(cfg, None, time.time())

    logger.info("serve стартовал: %s, макс %d поста/сутки, тик %dс",
                ", ".join(str(w) for w in schedule.windows),
                schedule.max_posts_per_day, tick)

    iterations = 0
    while not stopper.stopping:
        iterations += 1
        now = datetime.now(schedule.zone)
        state = load_state(cfg.state_path)
        day = state_day(state)

        if should_publish(now, schedule, day):
            logger.info("публикуем: %s", describe_day(day, schedule))
            result = run(cfg, dry_run=dry_run)
            if result.aborted:
                logger.warning("прогон прерван: %s", result.abort_reason)
            elif result.published:
                day = apply_post(day, now, schedule)
                _persist_day_counter(cfg, schedule, day, now)
                logger.info("опубликовано: %s", describe_day(day, schedule))
            elif result.verdict == "reject":
                logger.info("публикация отменена судьёй (reject)")
            else:
                # dry-run: run() вернул published=False, но цикл прошёл.
                logger.info("dry-run: цикл выполнен, публикация пропущена")
            if once:
                break
        else:
            slot = next_slot(now, schedule, day)
            slot_str = slot.strftime("%Y-%m-%d %H:%M") if slot else "завтра"
            # Тик вне окна публикации — это не «пора», это «ждём». В info
            # писать его каждые 60с бессмысленно: за сутки сотни строк шума.
            # Раз в час — heartbeat, остальное в debug.
            if now.minute == 0:
                logger.info("ждём: %s, следующий слот: %s",
                            describe_day(day, schedule), slot_str)
            else:
                logger.debug("ждём: %s, следующий слот: %s",
                             describe_day(day, schedule), slot_str)

        # Автоочистка логов раз в интервал.
        last_purge = _maybe_purge(cfg, last_purge, time.time())

        if once:
            break

        # Спим до следующей проверки, но не дольше чем до следующего слота.
        # wait — float: остаток до слота дробный, а time.sleep принимает float.
        wait: float = float(tick)
        if slot is not None:
            remaining = (slot - now).total_seconds()
            if remaining > 0:
                wait = min(wait, remaining)
        wait = max(1, wait)
        time.sleep(wait)

    logger.info("serve остановлен (итераций: %d)", iterations)
    return 0