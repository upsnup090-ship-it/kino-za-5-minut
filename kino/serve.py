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
    SlotConfig,
    SlotOccurrence,
    apply_post,
    describe_day,
    describe_slots,
    due_slot,
    is_late,
    lateness_minutes,
    missed_slots,
    next_occurrence,
    next_slot,
    parse_windows,
    should_publish,
)
from kino.schedule_db import ScheduleDb, ScheduleDbError
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


def build_slot_config(cfg: Config) -> SlotConfig:
    """Настройки режима слотов из конфига."""
    return SlotConfig(
        catchup_minutes=cfg.settings.schedule_catchup_minutes,
        late_grace_minutes=cfg.settings.schedule_late_grace_minutes,
        tz=cfg.settings.timezone,
    )


def _slot_rows(db: ScheduleDb) -> list[tuple[int, int, str]]:
    """Слоты из БД в форме, которую ждут функции планировщика."""
    return [(s.id, s.weekday, s.time) for s in db.list_slots(enabled_only=True)]


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


def _serve_slots(
    cfg: Config,
    *,
    dry_run: bool,
    once: bool,
    tick: int,
    stopper: Stopper,
) -> int:
    """Цикл serve в режиме `schedule.mode: slots`.

    Отличия от окон:
    - слоты читаются из БД на каждом тике, правка применяется без рестарта;
    - один пост на слот, суточного лимита и паузы нет (ритм задаёт сетка);
    - отработка пишется в `runs`, повторного срабатывания нет;
    - опоздание больше `catchup_minutes` — слот помечается `skipped`, не
      догоняется; публикация позже `late_grace_minutes` — с пометкой.
    """
    slot_cfg = build_slot_config(cfg)
    db = ScheduleDb(cfg.db_path)
    last_purge = _maybe_purge(cfg, None, time.time())
    iterations = 0

    try:
        slots = _slot_rows(db)
    except ScheduleDbError as exc:
        logger.error("слоты недоступны: %s", exc)
        return 1

    if not slots:
        logger.error(
            "режим slots, но в БД нет включённых слотов (%s). "
            "Заполни сетку: python -m kino schedule seed",
            cfg.db_path,
        )
        return 1

    logger.info(
        "serve стартовал (режим slots): %s, тик %dс, опоздание > %d мин — пропуск",
        describe_slots(slots),
        tick,
        slot_cfg.catchup_minutes,
    )

    while not stopper.stopping:
        iterations += 1
        now = datetime.now(slot_cfg.zone)
        wait: float = float(tick)

        try:
            slots = _slot_rows(db)
            if not slots:
                logger.warning("слоты удалены из БД на ходу, режим slots ждёт")
                time.sleep(max(1, tick))
                continue

            # 1. Просроченные слоты помечаем skipped, чтобы не догонять их.
            # Список уже отработанных берём из `runs` — иначе каждый тик
            # заново писал бы те же пропуски (за сутки простоя — сотни строк).
            missed_candidates = missed_slots(now, slots, slot_cfg)
            for missed in missed_candidates:
                if db.slot_done(missed.slot_id, missed.slot_at):
                    continue
                run_id = db.start_run(slot_id=missed.slot_id, slot_at=missed.slot_at)
                db.finish_run(
                    run_id,
                    outcome="skipped",
                    reason=f"опоздание больше {slot_cfg.catchup_minutes} мин",
                )
                logger.info("слот пропущен (не догоняем): %s", missed)

            # 2. Наступил ли слот и не отработан ли он.
            occurrence = due_slot(now, slots, slot_cfg)
            if occurrence is not None and db.slot_done(occurrence.slot_id, occurrence.slot_at):
                occurrence = None

            if occurrence is not None:
                logger.info("слот наступил: %s", occurrence)
                run_id = db.start_run(slot_id=occurrence.slot_id, slot_at=occurrence.slot_at)
                result = run(cfg, dry_run=dry_run)
                finished = datetime.now(slot_cfg.zone)

                if result.aborted:
                    db.finish_run(run_id, outcome="failed", reason=result.abort_reason or "прервано")
                    logger.warning("прогон прерван: %s", result.abort_reason)
                elif result.published:
                    late = lateness_minutes(occurrence, finished, slot_cfg)
                    reason = ""
                    if is_late(occurrence, finished, slot_cfg):
                        reason = f"опоздание {late} мин"
                        logger.warning("слот %s: публикация с опозданием %d мин", occurrence, late)
                    db.finish_run(
                        run_id,
                        outcome="published",
                        reason=reason,
                        link=result.link or None,
                        format=result.format,
                        kind=result.kind,
                    )
                    logger.info("опубликовано по слоту %s", occurrence)
                elif result.verdict == "reject":
                    db.finish_run(run_id, outcome="rejected", reason="судья отбил")
                    logger.info("слот %s: публикация отменена судьёй", occurrence)
                else:
                    # dry-run: цикл прошёл, но публикации нет.
                    db.finish_run(run_id, outcome="skipped", reason="dry-run")
                    logger.info("dry-run: слот %s отработан без публикации", occurrence)
                if once:
                    break
            else:
                upcoming = next_occurrence(now, slots, slot_cfg)
                slot_str = str(upcoming) if upcoming else "нет слотов"
                if now.minute == 0:
                    logger.info("ждём: следующий слот %s", slot_str)
                else:
                    logger.debug("ждём: следующий слот %s", slot_str)
                if upcoming is not None:
                    remaining = (upcoming.when - now).total_seconds()
                    if remaining > 0:
                        wait = min(wait, remaining)
        except ScheduleDbError as exc:
            # БД — не повод падать: планировщик переживёт один битый тик.
            logger.error("ошибка БД расписания на тике: %s", exc)
        except Exception as exc:  # noqa: BLE001 — цикл 24/7 не должен умирать
            logger.exception("необработанная ошибка в цикле слотов: %s", exc)

        last_purge = _maybe_purge(cfg, last_purge, time.time())

        if once:
            break

        wait = max(1.0, wait)
        time.sleep(wait)

    logger.info("serve остановлен (режим slots, итераций: %d)", iterations)
    return 0


def serve(cfg: Config, *, dry_run: bool = False, once: bool = False, tick_seconds: int | None = None) -> int:
    """Основной цикл. Возвращает код выхода (0 — нормально).

    tick_seconds: как часто проверять расписание; None — из конфига.
    once: один проход и выход (для отладки).
    """
    tick = tick_seconds or cfg.settings.tick_seconds

    stopper = Stopper()
    stopper.install()

    if cfg.settings.schedule_mode == "slots":
        # В режиме slots окна, суточный лимит и пауза не участвуют — сетка
        # сама расставляет ритм. Одна строка в логе, чтобы при разборе логов
        # не искать, почему windows из конфига ни на что не влияют.
        #
        # build_schedule вызывается только в ветке windows: в режиме slots
        # пустой список окон — нормальное состояние, и падать на нём нельзя.
        logger.info(
            "режим slots: windows/max_posts_per_day/min_gap_minutes игнорируются"
        )
        return _serve_slots(cfg, dry_run=dry_run, once=once, tick=tick, stopper=stopper)

    schedule = build_schedule(cfg)

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