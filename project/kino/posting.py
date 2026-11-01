"""Постинг по кнопке: тестовый пост в личку, финальный — в канал.

Две кнопки, разница между ними ровно одна — **куда уходит пост**:

- **тестовый** — в личку админу (`ADMIN_CHAT_ID`), канал не трогается,
  `state.json` и `used_links` не меняются. Показать, как пост выглядит,
  можно сколько угодно раз, и это не съедает новость.
- **финальный** — в канал, полный путь: `state`, дедупликация, счётчик дня,
  запись в `runs`, резерв. Ровно то же, что делает слот по расписанию.

Оба идут через `pipeline.run()`, а не через собственную сборку поста. Иначе
кнопка и расписание разойдутся, и «тестовый» перестанет быть тестом того,
что выйдет на самом деле.

Что здесь есть, а в `pipeline` нет:

- **сборка поста без публикации** (`build_only`) — прогон в dry-run, из
  которого берётся готовый пост: подпись, картинка, вариация, формат;
- **отправка готового поста в произвольный чат** (`deliver`) — потому что
  `publish()` всегда шлёт в `channel_chat_id`, а тестовому нужен личный;
- **журнал нажатий** — чтобы двойной тап не запустил два прогона: замок на
  прогон уже есть (`StateLock`), но второй тап должен получить понятный
  ответ, а не `RuntimeError`.

Долгий прогон (сбор + LLM + судья) идёт в отдельном потоке: бот не должен
замирать на 30–90 секунд, иначе Telegram отдаст таймаут и кнопка «залипнет».
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from kino.config import Config
from kino.pipeline import RunResult, run

logger = logging.getLogger(__name__)

#: Сколько живёт отметка «прогон по кнопке уже идёт». Прогон ограничен
#: `run.deadline_seconds`, но замок снимается по факту, а не по таймеру;
#: значение нужно только чтобы не залипнуть навсегда после падения потока.
RUN_STALE_SECONDS = 900


@dataclass
class Prepared:
    """Готовый пост, ещё не отправленный."""

    caption: str
    image_url: str
    kind: str
    format: str
    link: str = ""
    verdict: str = "accept"
    aborted: bool = False
    abort_reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.caption.strip()) and not self.aborted


@dataclass
class PostingState:
    """Отметка о текущем прогоне по кнопке. Одна на процесс."""

    started_at: float = 0.0
    mode: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)

    def busy(self) -> bool:
        if not self.started_at:
            return False
        if time.time() - self.started_at > RUN_STALE_SECONDS:
            logger.warning("постинг: отметка прогона устарела, снимаю")
            self.started_at = 0.0
            self.mode = ""
            return False
        return True

    def begin(self, mode: str) -> bool:
        with self.lock:
            if self.busy():
                return False
            self.started_at = time.time()
            self.mode = mode
            return True

    def end(self) -> None:
        with self.lock:
            self.started_at = 0.0
            self.mode = ""


#: Одна отметка на процесс — кнопки и слот не должны идти одновременно.
STATE = PostingState()


def build_only(cfg: Config) -> Prepared:
    """Собирает пост, ничего не публикуя и не трогая state.

    Это `run(dry_run=True)`: черновик, судья, картинка — всё настоящее, но
    `publish()` не вызывается, а `state.json` не пишется. Тот же код, что
    у боевого прогона, поэтому тестовый пост честно показывает результат.
    """
    result: RunResult = run(cfg, dry_run=True)
    return Prepared(
        caption=result.caption,
        image_url=result.image_url,
        kind=result.kind or "photo",
        format=result.format,
        link=result.link,
        verdict=result.verdict,
        aborted=result.aborted,
        abort_reason=result.abort_reason,
    )


def deliver(cfg: Config, prepared: Prepared, chat_id: str | int) -> bool:
    """Отправляет готовый пост в указанный чат.

    `publish()` из `kino/publish.py` всегда шлёт в канал, поэтому для теста
    нужен отдельный путь. Вариации повторяются те же, что в канале: photo,
    link, gallery, text — иначе тест показывал бы не то, что выйдет.
    """
    from kino import publish as pub

    if not prepared.ok:
        return False

    # `publish.send_*` принимают `str`: id чата приходит из Telegram числом,
    # но в URL он всё равно уходит строкой. Приводим здесь, один раз.
    target = str(chat_id)
    kind = prepared.kind
    if kind == "link" and prepared.link:
        return pub.send_link(cfg, target, prepared.caption, prepared.link)
    if kind == "gallery" and prepared.image_url:
        # Одно фото в галерее Telegram не примет (нужно 2+), и тогда честнее
        # отправить обычное фото, чем упасть в фолбэк.
        return pub.send_photo(cfg, target, prepared.image_url, prepared.caption)
    if kind == "text" or not prepared.image_url:
        return pub.send_text(cfg, target, prepared.caption)
    return pub.send_photo(cfg, target, prepared.image_url, prepared.caption)


def publish_now(cfg: Config) -> RunResult:
    """Финальный прогон: сбор и публикация в канал, полный путь.

    Не копия `run`, а он сам: `dry_run=False` включает state, дедупликацию,
    счётчик дня и запись в `runs`. Всё, что делает слот по расписанию.

    Отличие одно — запись в `runs` помечается `is_manual`: в журнале прогонов
    «пост по кнопке в 14:30» не должен выглядеть как сработавший слот.
    """
    from kino.schedule_db import ScheduleDb, ScheduleDbError

    try:
        db = ScheduleDb(cfg.db_path)
        run_id = db.start_run(
            slot_id=None,
            slot_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            is_manual=True,
            manual_reason="кнопка: финальный пост",
        )
    except ScheduleDbError as exc:
        # Журнал недоступен — публикация всё равно важнее записи о ней.
        logger.warning("постинг: не удалось открыть запись прогона (%s)", exc)
        return run(cfg, dry_run=False)

    try:
        result = run(cfg, dry_run=False)
    except Exception as exc:  # noqa: BLE001 — запись не должна остаться открытой
        try:
            db.finish_run(run_id, outcome="failed", reason=f"кнопка: {exc}")
        except ScheduleDbError:
            logger.debug("постинг: не удалось закрыть запись прогона %s", run_id)
        raise

    try:
        if result.published:
            db.finish_run(
                run_id,
                outcome="published",
                reason="кнопка",
                link=result.link or None,
                format=result.format,
                kind=result.kind,
                caption_len=len(result.caption) or None,
                quality_score=result.quality_score,
            )
        elif result.verdict == "reject":
            db.finish_run(run_id, outcome="rejected", reason="кнопка: судья отбил")
        elif result.aborted:
            db.finish_run(run_id, outcome="failed", reason=f"кнопка: {result.abort_reason}")
        else:
            db.finish_run(run_id, outcome="failed", reason="кнопка: публикация не удалась")
    except ScheduleDbError as exc:
        logger.warning("постинг: не удалось закрыть запись прогона %s (%s)", run_id, exc)

    return result


def preview_text(prepared: Prepared, *, to_channel: bool) -> str:
    """Короткая шапка над тестовым постом: что это и куда пойдёт."""
    where = "канал" if to_channel else "личка (тест)"
    if not prepared.ok:
        reason = prepared.abort_reason or "пустой пост"
        return f"🧪 Тестовый пост не собран: {reason}"
    lines = [f"🧪 Тестовый пост — {where}", f"вариация: {prepared.kind}, формат: {prepared.format}"]
    if prepared.link:
        lines.append(f"материал: {prepared.link}")
    lines.append("")
    return "\n".join(lines)


def describe_result(result: RunResult, *, to_channel: bool) -> str:
    """Итог прогона одной строкой для чата."""
    where = "в канал" if to_channel else "в личку"
    if result.aborted:
        return f"⏱ Прогон прерван: {result.abort_reason}"
    if result.published:
        return f"✅ Пост ушёл {where}: {result.kind}, формат {result.format}"
    if result.verdict == "reject":
        return "🚫 Судья отбил черновик — пост не отправлен"
    return f"⚠️ Пост не ушёл {where} (публикация вернула False)"
