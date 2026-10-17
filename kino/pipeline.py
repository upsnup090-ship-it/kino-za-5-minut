"""Связывает шаги пайплайна: collect → choose → draft → judge → image → caption → publish."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from kino.caption import escape_html, truncate_caption
from kino.choose import Choice, choose
from kino.collect import collect
from kino.config import Config
from kino.draft import draft
from kino.horizon import HorizonError, collect_horizon
from kino.image import find_image
from kino.judge import judge
from kino.publish import publish
from kino.state import State, StateLock, load_state, record_choice, save_state

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    caption: str
    image_url: str
    format: str
    published: bool
    dry_run: bool


def record_format_stat(cfg: Config, choice: Choice) -> None:
    """Дописывает строку в CSV статистики форматов. Ошибки записи не роняют пайплайн."""
    if not cfg.stats_path:
        return
    try:
        cfg.stats_path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not cfg.stats_path.exists()
        with cfg.stats_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            if new_file:
                writer.writerow(["timestamp", "format", "source", "quality_score"])
            writer.writerow(
                [
                    datetime.now(timezone.utc).isoformat(),
                    choice.format,
                    choice.item.source,
                    choice.quality_score,
                ]
            )
    except OSError as exc:
        logger.warning("не удалось записать статистику формата: %s", exc)


def build_caption(draft_data: dict, image_url: str, cfg: Config) -> str:
    """Собирает подпись из черновика. Текст экранируется для parse_mode=HTML."""
    headline = escape_html(str(draft_data.get("headline", "")).strip())
    text = escape_html(str(draft_data.get("text", "")).strip())
    question = escape_html(str(draft_data.get("question", "")).strip())

    blocks = [b for b in (headline, text) if b]
    if question:
        blocks.append("—————")
        blocks.append(f"❓ {question}")

    caption = "\n\n".join(blocks)
    return truncate_caption(
        caption,
        max_length=cfg.settings.caption_max_length,
        hard_limit=cfg.settings.caption_hard_limit,
    )


def run(cfg: Config, dry_run: bool = True) -> RunResult:
    """Полный прогон одного поста."""
    lock = StateLock(cfg.state_path.with_suffix(".lock"))
    if not lock.acquire():
        raise RuntimeError("другой запуск уже выполняется (lock занят)")

    try:
        state = load_state(cfg.state_path)
        used_links = set(state.used_links)
        used_sigs = state.used_sigs

        if cfg.source_mode == "horizon":
            collected = collect_horizon(
                cfg.horizon_dir,
                cfg.horizon_summaries_dir,
                run=cfg.horizon_run,
                hours=cfg.horizon_hours,
                timeout=cfg.horizon_timeout,
            )
        else:
            collected = collect(cfg)
        choice: Choice = choose(collected.items, cfg, used_links, used_sigs)

        record_format_stat(cfg, choice)

        draft_data = draft(choice, cfg)
        final_data = judge(choice, draft_data, cfg)

        wiki_title = str(final_data.get("wiki", "")).strip()
        image = find_image(wiki_title, cfg)

        caption = build_caption(final_data, image.url, cfg)

        published = False
        if not dry_run:
            published = publish(cfg, caption, image.url, dry_run=False)
            if published:
                record_choice(
                    state,
                    choice.item.link,
                    _signature_of(choice),
                    cfg.settings.state_max_links,
                    cfg.settings.state_max_signatures,
                )
                save_state(
                    cfg.state_path,
                    state,
                    cfg.settings.state_max_links,
                    cfg.settings.state_max_signatures,
                )

        return RunResult(
            caption=caption,
            image_url=image.url,
            format=choice.format,
            published=published,
            dry_run=dry_run,
        )
    finally:
        lock.release()


def _signature_of(choice: Choice) -> list[str]:
    from kino.choose import _signature

    return _signature(choice.item.title)