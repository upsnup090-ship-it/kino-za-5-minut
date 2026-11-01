"""Навигация между экранами и пауза публикаций."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from kino.config import Config
from kino.schedule_db import ScheduleDb, ScheduleDbError

from kino.admin.common import Reply, _btn
from kino.admin.posting import handle_post_button
from kino.admin.screens import (
    _last_run_line,
    _reserve_stats,
    _safe_paused,
    _serve_state_line,
    screen_about,
    screen_autoposting,
    screen_backup,
    screen_home,
    screen_preview,
    screen_runs,
    screen_schedule,
)


def _handle_nav(cfg: Config, arg: str, now: datetime | None) -> Reply:
    if arg in ("home", "start"):
        return screen_home(cfg)
    if arg == "autoposting":
        return screen_autoposting(cfg)
    if arg == "about":
        return screen_about(cfg)
    if arg == "backup":
        return screen_backup(cfg)
    if arg in ("sched", "schedule"):
        return screen_schedule(cfg, now)
    if arg == "next7":
        return screen_preview(cfg, now)
    if arg == "reserve":
        return screen_reserve(cfg)
    if arg == "today":
        return screen_today(cfg, now)
    if arg.startswith("runs"):
        return screen_runs(cfg)
    if arg == "runnow":
        return handle_post_button(cfg, "final", now)
    if arg == "state":
        return screen_state(cfg)
    if arg == "state:refresh":
        return screen_state(cfg)
    if arg == "state:errors":
        return screen_state_errors(cfg)
    if arg == "backup:logs":
        return Reply(text="📜 Логи\n\nРаздел в работе: здесь будут просмотр и выгрузка логов.", keyboard=[[_btn("◀️ Назад", "nav:backup")]])
    if arg == "backup:make":
        return Reply(text="💾 Бэкап\n\nРаздел в работе: здесь будет создание резервной копии.", keyboard=[[_btn("◀️ Назад", "nav:backup")]])
    return screen_home(cfg)


def _handle_state(cfg: Config, arg: str, now: datetime | None) -> Reply:
    if arg == "pause":
        db = ScheduleDb(cfg.db_path)
        try:
            was_paused = db.is_paused()
            db.set_paused(not was_paused)
        except ScheduleDbError as exc:
            return Reply(text=f"❌ Не удалось переключить паузу: {exc}", keyboard=[[_btn("◀️ Назад", "nav:autoposting")]])
        reply = screen_autoposting(cfg)
        head = "⏸ Публикации на паузе. Слоты не публикуются и не пропускаются — после снятия вернутся в работу." if not was_paused else "▶️ Пауза снята, публикации по расписанию снова идут."
        reply.text = f"{head}\n\n{reply.text}"
        return reply
    return screen_autoposting(cfg)


def screen_state(cfg: Config) -> Reply:
    db = ScheduleDb(cfg.db_path)
    paused = _safe_paused(db)
    available, total = _reserve_stats(cfg)
    lines = ["📡 Состояние автопубликации", "", _serve_state_line(db), _last_run_line(db), f"резерв: {available} из {total}", f"пауза: {'включена' if paused else 'снята'}"]
    return Reply(text="\n".join(lines), keyboard=[
        [_btn("🔄 Обновить", "nav:state:refresh"), _btn("📜 Ошибки", "nav:state:errors")],
        [_btn("◀️ Назад", "nav:autoposting")],
    ])


def screen_state_errors(cfg: Config) -> Reply:
    db = ScheduleDb(cfg.db_path)
    now = datetime.now().astimezone()
    since = now - timedelta(hours=24)
    try:
        records = db.recent_runs(limit=200)
    except ScheduleDbError:
        records = []
    bad = []
    for rec in records:
        if rec.outcome not in ("failed", "rejected"):
            continue
        try:
            started = datetime.fromisoformat(rec.started_at)
        except ValueError:
            continue
        if started >= since:
            bad.append(rec)
    lines = ["📜 Последние ошибки (24ч)", ""]
    if not bad:
        lines.append("ошибок не было")
    else:
        for rec in bad[:20]:
            stamp = rec.slot_at.replace("T", " ")[:16]
            reason = rec.reason or "без причины"
            lines.append(f"{stamp} · {rec.outcome} — {reason}")
    return Reply(text="\n".join(lines), keyboard=[
        [_btn("🔄 Обновить", "nav:state:errors")],
        [_btn("◀️ Назад", "nav:state")],
    ])


def screen_reserve(cfg: Config) -> Reply:
    available, total = _reserve_stats(cfg)
    lines = ["🗄 Резерв", "", f"доступно: {available} из {total}", "", "В резерв попадает готовый пост после реальной публикации — страховка от пустого слота. Просроченные не берутся."]
    return Reply(text="\n".join(lines), keyboard=[[_btn("◀️ Назад", "nav:autoposting")]])


def screen_today(cfg: Config, now: datetime | None = None) -> Reply:
    db = ScheduleDb(cfg.db_path)
    moment = now or datetime.now()
    yesterday = moment.date() - timedelta(days=1)
    try:
        records = db.recent_runs(limit=200)
    except ScheduleDbError:
        return Reply(text="📊 Итог за вчера\n\nжурнал недоступен", keyboard=[[_btn("◀️ Назад", "nav:autoposting")]])
    day_records: list[Any] = []
    for rec in records:
        try:
            rec_date = datetime.fromisoformat(rec.slot_at).date()
        except ValueError:
            continue
        if rec_date == yesterday:
            day_records.append(rec)
    if not day_records:
        return Reply(text=f"📊 Итог за {yesterday.strftime('%d.%m')}\n\nзаписей нет — не было ни публикаций, ни пропусков", keyboard=[[_btn("◀️ Назад", "nav:autoposting")]])
    published = [r for r in day_records if r.outcome == "published"]
    skipped = [r for r in day_records if r.outcome == "skipped"]
    failed = [r for r in day_records if r.outcome == "failed"]
    qualities = [r.quality_score for r in published if r.quality_score is not None]
    lines = [f"📊 Итог за {yesterday.strftime('%d.%m')}", "", f"опубликовано: {len(published)}"]
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
    return Reply(text="\n".join(lines), keyboard=[[_btn("◀️ Назад", "nav:autoposting")]])
