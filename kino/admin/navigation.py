"""Навигация между экранами и пауза публикаций.

`nav:<экран>` — переходы. `state:pause` — переключатель паузы: слоты не
публикуются и не пропускаются, после снятия возвращаются в работу.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from kino.config import Config
from kino.schedule_db import ScheduleDb, ScheduleDbError

from kino.admin.common import Reply, _btn
from kino.admin.screens import (
    _reserve_stats,
    screen_home,
    screen_preview,
    screen_runs,
    screen_schedule,
)

# --- навигация и пауза -------------------------------------------------------


def _handle_nav(cfg: Config, arg: str, now: datetime | None) -> Reply:
    """Переход между экранами по `nav:<экран>`.

    `nav:today` — не «вчера» и не «сегодня» наугад: экран называется
    «Итог за вчера», и колбэк обязан означать ровно это (задача #78).
    Неизвестный переход возвращает главный экран, а не исключение.
    """
    if arg in ("full", "compact"):
        return screen_home(cfg, view=arg)
    if arg == "sched":
        return screen_schedule(cfg, now)
    if arg == "next7":
        return screen_preview(cfg, now)
    if arg == "reserve":
        return screen_reserve(cfg)
    if arg == "today":
        return screen_today(cfg, now)
    if arg.startswith("runs"):
        return screen_runs(cfg)
    if arg in ("state", "state:refresh"):
        return screen_home(cfg, view="full")
    return screen_home(cfg)


def _handle_state(cfg: Config, arg: str, now: datetime | None) -> Reply:
    """Действия над публикацией: `state:pause`.

    Пауза — не «пропустить слоты»: при снятии слот возвращается в работу.
    Иначе пауза на час съела бы слот до конца недели.
    """
    if arg == "pause":
        db = ScheduleDb(cfg.db_path)
        try:
            was_paused = db.is_paused()
            db.set_paused(not was_paused)
        except ScheduleDbError as exc:
            return Reply(
                text=f"❌ Не удалось переключить паузу: {exc}",
                keyboard=[[_btn("◀️ Назад", "nav:full")]],
            )
        reply = screen_home(cfg, view="full")
        head = (
            "⏸ Публикации на паузе. Слоты не публикуются и не пропускаются — "
            "после снятия вернутся в работу."
            if not was_paused
            else "▶️ Пауза снята, публикации по расписанию снова идут."
        )
        reply.text = f"{head}\n\n{reply.text}"
        return reply
    return screen_home(cfg, view="full")


def screen_reserve(cfg: Config) -> Reply:
    """Резерв: сколько постов доступно."""
    available, total = _reserve_stats(cfg)
    lines = [
        "🗄 Резерв",
        "",
        f"доступно: {available} из {total}",
        "",
        "В резерв попадает готовый пост после реальной публикации —",
        "страховка от пустого слота. Просроченные не берутся.",
    ]
    return Reply(
        text="\n".join(lines),
        keyboard=[[_btn("◀️ Назад", "nav:full")]],
    )


def screen_today(cfg: Config, now: datetime | None = None) -> Reply:
    """Итог за вчера: что вышло, что пропущено, средняя оценка.

    Считается по `runs` за прошлые сутки. Пусто — так и говорим: нулевой
    отчёт («всё хорошо, 0 постов») хуже честного «за вчера записей нет».
    """
    db = ScheduleDb(cfg.db_path)
    moment = now or datetime.now()
    yesterday = moment.date() - timedelta(days=1)

    try:
        records = db.recent_runs(limit=200)
    except ScheduleDbError:
        return Reply(
            text="📊 Итог за вчера\n\nжурнал недоступен",
            keyboard=[[_btn("◀️ Назад", "nav:full")]],
        )

    day_records: list[Any] = []
    for rec in records:
        try:
            rec_date = datetime.fromisoformat(rec.slot_at).date()
        except ValueError:
            continue
        if rec_date == yesterday:
            day_records.append(rec)

    if not day_records:
        return Reply(
            text=(
                f"📊 Итог за {yesterday.strftime('%d.%m')}\n\n"
                "записей нет — не было ни публикаций, ни пропусков"
            ),
            keyboard=[[_btn("◀️ Назад", "nav:full")]],
        )

    published = [r for r in day_records if r.outcome == "published"]
    skipped = [r for r in day_records if r.outcome == "skipped"]
    failed = [r for r in day_records if r.outcome == "failed"]
    qualities = [r.quality_score for r in published if r.quality_score is not None]

    lines = [f"📊 Итог за {yesterday.strftime('%d.%m')}", ""]
    lines.append(f"опубликовано: {len(published)}")
    if skipped:
        lines.append(f"пропущено: {len(skipped)}")
    if failed:
        lines.append(f"сбоев: {len(failed)}")
    if qualities:
        avg = sum(qualities) / len(qualities)
        lines.append(f"средняя оценка: {avg:.1f}")
    lines.append("")
    for rec in published:
        stamp = rec.slot_at.replace("T", " ")[11:16]
        tail = f"{stamp} · {rec.format or '—'}"
        if rec.caption_len:
            tail += f" · {rec.caption_len} симв."
        if rec.quality_score is not None:
            tail += f" · q{rec.quality_score:g}"
        if rec.is_manual:
            tail += " · ✋"
        lines.append(tail)
    return Reply(
        text="\n".join(lines),
        keyboard=[[_btn("◀️ Назад", "nav:full")]],
    )

