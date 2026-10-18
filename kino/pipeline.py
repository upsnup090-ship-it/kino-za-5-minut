"""Связывает шаги пайплайна: collect → choose → draft → judge → image → caption → publish."""

from __future__ import annotations

import csv
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from kino.caption import build_caption as build_caption_text
from kino.caption import caption_limit_for, truncate_caption
from kino.choose import Choice, choose
from kino.collect import collect
from kino.config import Config
from kino.deadline import Deadline, check
from kino.draft import draft
from kino.horizon import HorizonError, collect_horizon
from kino.image import ImageResult, find_image_multi, find_images
from kino.judge import judge
from kino.publish import publish
from kino.state import State, StateLock, load_state, record_choice, save_state

logger = logging.getLogger(__name__)

# Вариации поста: photo — фото с подписью, link — пост-ссылка с превью,
# article — статья на graph.org, уходит постом-ссылкой на неё, text — текст
# без картинки,
# poll — опрос, gallery — карусель фото.
_VARIATIONS = ("photo", "link", "article", "text", "poll", "gallery")

# Форматы контента, которые диктуют конкретную вариацию доставки.
# Остальные форматы (facts, news, when, quote, behind_scenes) идут через
# случайный выбор pick_variation.
_FORMAT_TO_KIND: dict[str, str] = {
    "video": "link",         # video_url → пост-ссылка с YouTube-превью
    "infographic": "photo",  # инфографика рендерится как картинка
    "collage": "gallery",    # обзор с несколькими картинками
    "interview": "article",  # диалог уходит статьёй
    "poll": "poll",          # опрос
    "trailer": "link",       # трейлер → ссылка с превью видео
}


def _safe_int(value: Any, default: int = 0) -> int:
    """Приводит вес к int, не роняя прогон на мусоре из конфига.

    Опечатка в settings.yaml (`photo: "35"` в кавычках, `photo: много`) раньше
    валила ValueError уже после того, как токены LLM были потрачены.
    """
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        logger.warning("вес вариации %r не число, считаем %d", value, default)
        return default


def pick_variation(
    cfg: Config,
    draft_data: dict,
    image_url: str,
    choice: Choice,
) -> tuple[str, str]:
    """Выбирает вариацию поста. Возвращает (kind, link_url).

    Если формат контента диктует конкретную вариацию (video→link,
    infographic→photo, collage→gallery, interview→article, poll→poll,
    trailer→link) — используется она. Иначе — случайный выбор по весам.

    link_url: для link — ссылка новости или video_url; для article —
    заполнится позже в run(); здесь пусто.
    """
    # Формат контента может диктовать вариацию доставки.
    forced_kind = _FORMAT_TO_KIND.get(choice.format, "")

    if forced_kind:
        if forced_kind == "link":
            # Для video/trailer — берём video_url из черновика, иначе фолбэк
            # на ссылку новости.
            video_url = str(draft_data.get("video_url", "") or "").strip()
            if video_url:
                logger.info("вариация продиктована форматом %s: %s (video_url)",
                            choice.format, forced_kind)
                return "link", video_url
            link = choice.item.link
            if link:
                logger.info("вариация продиктована форматом %s: %s (link новости)",
                            choice.format, forced_kind)
                return "link", link
            logger.warning("формат %s без ссылки — фолбэк на photo", choice.format)
            return "photo", ""
        logger.info("вариация продиктована форматом %s: %s", choice.format, forced_kind)
        return forced_kind, ""

    # Свободный формат — случайный выбор по весам.
    weights_raw = getattr(cfg.settings, "post_variations", {}) or {}
    if isinstance(weights_raw, dict):
        unknown = sorted(set(weights_raw) - set(_VARIATIONS))
        if unknown:
            logger.warning("post_variations: неизвестные ключи пропущены: %s",
                           ", ".join(unknown))
    else:
        logger.warning("post_variations не словарь (%s), берём photo",
                       type(weights_raw).__name__)
        weights_raw = {}

    weights = [_safe_int(weights_raw.get(v, 0)) for v in _VARIATIONS]
    if not any(weights):
        weights = [1, 0, 0, 0, 0, 0]  # не настроено — только фото, как раньше
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
    verdict: str = "accept"
    #: Вариация, которой пост реально ушёл (после всех фолбэков), и та, что была
    #: выбрана изначально. По логу и результату должно быть видно одно и то же.
    kind: str = ""
    requested_kind: str = ""
    #: Прогон не успел завершить пост из-за исчерпания бюджета времени.
    aborted: bool = False
    abort_reason: str = ""


def _reject_run(cfg: Config, choice: Choice, state: State, reason: str, *, dry_run: bool) -> None:
    """Обрабатывает отклонённый судьёй черновик: не публикует, помечает ссылку.

    Ссылку записываем в state как использованную, иначе следующий прогон
    выберет ту же новость, снова упрётся в reject — и так по кругу. В dry-run
    state не трогаем: это гарантия режима, и reject не должен её нарушать.
    """
    logger.warning("судья отклонил черновик, публикация отменена: %s", reason or "без причины")
    if dry_run:
        logger.info("dry-run: state не изменён (reject)")
    else:
        record_choice(
            state,
            choice.item.link,
            _signature_of(choice),
            cfg.settings.state_max_links,
            cfg.settings.state_max_signatures,
        )
        try:
            save_state(
                cfg.state_path,
                state,
                cfg.settings.state_max_links,
                cfg.settings.state_max_signatures,
            )
        except OSError as exc:
            logger.warning("не удалось сохранить state после reject: %s", exc)
    try:
        from kino.publish import notify_admin

        notify_admin(cfg, f"судья отклонил новость ({reason or 'без причины'}): {choice.item.title[:120]}")
    except Exception:  # noqa: BLE001 — уведомление не должно ронять прогон
        pass


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
    """Собирает подпись из черновика (единственный стиль, см. kino.caption)."""
    return build_caption_text(draft_data, image_url, cfg)


def run(cfg: Config, dry_run: bool = True, *, deadline: Deadline | None = None) -> RunResult:
    """Полный прогон одного поста.

    Дедлайн ограничивает бюджет времени: если не передан, берётся из конфига
    (`deadline_seconds`). При исчерпании прогон завершается с `aborted=True`,
    а публикация готового поста не отменяется — она важнее добивки картинки.
    """
    if deadline is None:
        deadline = Deadline(cfg.settings.deadline_seconds)

    def _abort(reason: str) -> RunResult:
        logger.warning("прогон прерван: %s", reason)
        return RunResult(
            caption="",
            image_url="",
            format="",
            published=False,
            dry_run=dry_run,
            aborted=True,
            abort_reason=reason,
        )

    lock = StateLock(cfg.state_path.with_suffix(".lock"))
    if not lock.acquire():
        raise RuntimeError("другой запуск уже выполняется (lock занят)")

    try:
        state = load_state(cfg.state_path)
        used_links = set(state.used_links)
        used_sigs = state.used_sigs

        if not check(deadline, step="сбор"):
            return _abort("бюджет исчерпан до сбора")
        if cfg.source_mode == "horizon":
            collected = collect_horizon(
                cfg.horizon_dir,
                cfg.horizon_summaries_dir,
                run=cfg.horizon_run,
                hours=cfg.horizon_hours,
                timeout=deadline.slice(cfg.horizon_timeout),
            )
        else:
            collected = collect(cfg, deadline=deadline)
        if not collected.items:
            return _abort(f"сбор не дал записей (broken={len(collected.broken)})")
        choice: Choice = choose(collected.items, cfg, used_links, used_sigs)

        record_format_stat(cfg, choice)

        if not check(deadline, step="черновик"):
            return _abort("бюджет исчерпан до черновика")
        draft_data = draft(choice, cfg, deadline=deadline)
        if not check(deadline, step="судья"):
            return _abort("бюджет исчерпан до судьи")
        final_data = judge(choice, draft_data, cfg, deadline=deadline)

        verdict = str(final_data.get("verdict", "accept"))
        if verdict == "reject":
            reason = str(final_data.get("reason", "") or "").strip()
            _reject_run(cfg, choice, state, reason, dry_run=dry_run)
            return RunResult(
                caption="",
                image_url="",
                format=choice.format,
                published=False,
                dry_run=dry_run,
                verdict=verdict,
            )

        wiki_title = str(final_data.get("wiki", "")).strip()
        # Картинка — самый низкоприоритетный шаг: если бюджет на исходе,
        # пост уходит без неё. «Чужая» картинка всё равно не подставляется.
        if check(deadline, step="картинка"):
            image = find_image_multi(
                [
                    wiki_title,
                    str(final_data.get("film", "")).strip(),
                    str(final_data.get("director", "")).strip(),
                ],
                cfg,
            )
        else:
            logger.warning("бюджет исчерпан, картинку пропускаем")
            image = ImageResult()

        caption = build_caption(final_data, image.url, cfg)

        # Пишем статью и язык картинки в лог: без этого при разборе «картинка не
        # та» непонятно, по какому кандидату она нашлась (поля ImageResult.title
        # и .lang иначе заполнялись и нигде не читались).
        if image.url:
            logger.info("картинка: %s (%s, %s)", image.title or "без названия", image.lang, image.url)
        else:
            logger.info("картинка не найдена, кандидаты: wiki=%r film=%r director=%r",
                        wiki_title, final_data.get("film"), final_data.get("director"))

        kind, link_url = pick_variation(cfg, final_data, image.url, choice)
        requested_kind = kind
        published = False
        extra_images: list[str] = []
        poll_q, poll_opts = "", []
        # --- инфографика: рендерим картинку через QuickChart ---
        infographic_url = ""
        if choice.format == "infographic":
            from kino.infographic import build_url as infographic_build_url
            from kino.infographic import validate as infographic_validate

            info_title, info_facts = infographic_validate(
                final_data.get("infographic")
            )
            if info_facts:
                infographic_url = infographic_build_url(info_title, info_facts)
                if infographic_url:
                    logger.info("инфографика: %s (%d фактов)", info_title, len(info_facts))
                    # Подменяем картинку на инфографику.
                    image = ImageResult(url=infographic_url, title=info_title, lang="infographic")
                else:
                    logger.warning("инфографика: не удалось собрать URL")
            else:
                logger.warning("инфографика: нет данных от LLM, фолбэк на текстовый пост")
                if kind == "photo" and not image.url:
                    kind = "text"
        # --- коллаж: ищем картинки по image_candidates ---
        if choice.format == "collage" and kind == "gallery":
            candidates = final_data.get("image_candidates")
            if isinstance(candidates, list) and len(candidates) >= 2:
                extra_images = find_images(
                    wiki_title,
                    [str(c) for c in candidates if str(c).strip()],
                    cfg,
                )
                if len(extra_images) < 2:
                    logger.warning("коллаж: не хватило картинок по image_candidates, фолбэк")
                    # Пробуем стандартный поиск.
                    extra_images = find_images(
                        wiki_title,
                        [str(final_data.get("film", "")), str(final_data.get("director", ""))],
                        cfg,
                    )
        if kind == "article":
            link_url = _publish_article(cfg, final_data, image.url, choice)
            if link_url:
                kind = "link"  # статья уходит постом-ссылкой с превью
            else:
                kind = "photo"  # статьйник недоступен — фолбэк на фото
        if kind == "gallery":
            extra_images = find_images(
                wiki_title,
                [str(final_data.get("film", "")), str(final_data.get("director", ""))],
                cfg,
            )
            if len(extra_images) < 2:
                kind = "photo"  # не нашли 2+ картинок — фолбэк
        if kind == "poll":
            poll_q, poll_opts = _build_poll(final_data)
            if not poll_q:
                kind = "photo"  # нет вопроса — фолбэк
        # Вариации с картинкой без картинки не имеют смысла: photo/gallery уходят
        # текстом. Раньше лог показывал «gallery», а в канал шёл текст — и по
        # логу нельзя было понять, что реально опубликовано.
        if kind in ("photo", "gallery") and not image.url:
            logger.warning("вариация %s без картинки — уходим текстом", kind)
            kind = "text"
        if kind != requested_kind:
            logger.info("вариация поста: %s (выбрана %s, фолбэк)", kind, requested_kind)
        else:
            logger.info("вариация поста: %s", kind)

        # Подпись режется по лимиту своей вариации: у фото и карусели это 1024
        # символа, у текста и ссылки — 4096. Считаем после выбора вариации,
        # иначе текстовые посты резались по лимиту фото.
        caption = truncate_caption(
            caption,
            max_length=caption_limit_for(kind),
            hard_limit=caption_limit_for(kind),
        )

        if not dry_run:
            published = publish(
                cfg,
                caption,
                image.url if kind != "text" else "",
                dry_run=False,
                kind=kind,
                link_url=link_url,
                extra_images=extra_images if kind == "gallery" else None,
                poll_question=poll_q if kind == "poll" else "",
                poll_options=poll_opts if kind == "poll" else None,
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
            verdict=verdict,
            kind=kind,
            requested_kind=requested_kind,
        )
    finally:
        lock.release()


def _build_poll(draft_data: dict) -> tuple[str, list[str]]:
    """Вопрос и варианты для опроса из черновика.

    Если LLM вернул poll_options — берём их, иначе нейтральный набор.
    """
    question = str(draft_data.get("question", "")).strip()
    if not question:
        return "", []
    raw = draft_data.get("poll_options")
    options: list[str] = []
    if isinstance(raw, list):
        options = [str(o).strip() for o in raw if str(o).strip()]
    if len(options) < 2:
        options = ["Да, конечно", "Нет", "Посмотрим", "Свой вариант в комментариях"]
    return question[:300], options[:6]


def _publish_article(cfg: Config, draft_data: dict, image_url: str, choice: Choice) -> str:
    """Создаёт страницу статьи (graph.org). Ошибка → '' (фолбэк на фото)."""
    try:
        from kino.article import ArticleError, create_page, load_token

        token = load_token(cfg.state_path.parent / "graph_token.txt")
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
    except ArticleError as exc:
        logger.warning("статья недоступна: %s — фолбэк на фото", exc)
        return ""
    except Exception as exc:  # noqa: BLE001 — статья не должна ронять пост
        logger.warning("ошибка статьи: %s — фолбэк на фото", exc.__class__.__name__)
        return ""


def _signature_of(choice: Choice) -> list[str]:
    from kino.choose import _signature

    return _signature(choice.item.title)