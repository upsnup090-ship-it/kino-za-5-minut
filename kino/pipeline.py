"""Связывает шаги пайплайна: collect → choose → draft → judge → image → caption → publish."""

from __future__ import annotations

import csv
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone

from kino.caption import build_caption as build_caption_text
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

# Вариации поста: photo — фото с подписью, link — пост-ссылка с превью
# (в т.ч. YouTube-видео), article — статья Telegraph + пост-ссылка на неё.
_VARIATIONS = ("photo", "link", "article")


def pick_variation(
    cfg: Config,
    draft_data: dict,
    image_url: str,
    choice: Choice,
) -> tuple[str, str]:
    """Случайно выбирает вариацию поста. Возвращает (kind, link_url).

    link_url: для link — ссылка новости (превью в посте),
    для article — заполнится позже в run(); здесь пусто.
    """
    weights_raw = getattr(cfg.settings, "post_variations", {}) or {}
    weights = [max(0, int(weights_raw.get(v, 0))) for v in _VARIATIONS]
    if not any(weights):
        weights = [1, 0, 0]  # не настроено — только фото, как раньше
    kind = random.choices(_VARIATIONS, weights=weights, k=1)[0]

    if kind == "link":
        link = choice.item.link
        if not link:
            logger.warning("вариация link без ссылки новости — фолбэк на photo")
            return "photo", ""
        return "link", link
    return kind, ""


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
    """Собирает подпись из черновика. Делегирует в kino.caption.build_caption,
    который сам выбирает новый или старый стиль по набору полей."""
    return build_caption_text(draft_data, image_url, cfg)


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

        kind, link_url = pick_variation(cfg, final_data, image.url, choice)
        published = False
        if kind == "article":
            link_url = _publish_article(cfg, final_data, image.url, choice)
            if link_url:
                kind = "link"  # статья уходит постом-ссылкой с превью
            else:
                kind = "photo"  # telegraph недоступен — фолбэк на фото
        if not dry_run:
            published = publish(
                cfg,
                caption,
                image.url,
                dry_run=False,
                kind=kind,
                link_url=link_url,
            )
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


def _publish_article(cfg: Config, draft_data: dict, image_url: str, choice: Choice) -> str:
    """Создаёт страницу Telegraph из черновика. Ошибка → '' (фолбэк на фото)."""
    try:
        from kino.telegraph import TelegraphError, create_page, load_token

        token = load_token(cfg.state_path.parent / "telegraph_token.txt")
        title = str(draft_data.get("headline", "")).strip() or "Кино за 5 минут"
        body_parts = [
            str(draft_data.get("text", "")).strip(),
            str(draft_data.get("key_moment", "")).strip(),
        ]
        body = "\n\n".join(p for p in body_parts if p)
        question = str(draft_data.get("question", "")).strip()
        if question:
            body += f"\n\n❓ {question}"
        return create_page(
            token,
            title,
            body,
            image_url=image_url,
            source_url=choice.item.link,
        )
    except TelegraphError as exc:
        logger.warning("telegraph недоступен: %s — фолбэк на фото", exc)
        return ""
    except Exception as exc:  # noqa: BLE001 — статья не должна ронять пост
        logger.warning("ошибка статьи: %s — фолбэк на фото", exc.__class__.__name__)
        return ""


def _signature_of(choice: Choice) -> list[str]:
    from kino.choose import _signature

    return _signature(choice.item.title)