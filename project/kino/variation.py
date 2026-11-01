"""Слой вариаций поста: какой вид публикации и что для него нужно.

Формат (16 штук, formats.yaml) отвечает на вопрос «О ЧЕМ пост», вариация
(6 штук) — «КАК его доставить в канал»: photo | link | article | text |
poll | gallery.

Здесь живёт вся вариационная логика, которую раньше держал pipeline.run():
- pick_variation: формат → kind (9 форматов диктуют, 7 — случайный по весам);
- подготовка материала вариации (картинка, карусель, опрос, статья,
  инфографика);
- фолбэки в одном месте — resolve_variation(), единственная точка, где kind
  может «переехать» (link без ссылки → photo и т.д.).

pipeline.run() вызывает resolve_variation() и дальше только публикует.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

from kino.caption import format_fields_plain, md_bold_to_html
from kino.choose import Choice
from kino.config import Config
from kino.deadline import Deadline, check
from kino.image import ImageResult, find_image_multi, find_images

logger = logging.getLogger(__name__)

# Вариации поста: photo — фото с подписью, link — пост-ссылка с превью,
# article — статья на graph.org, уходит постом-ссылкой на неё, text — текст
# без картинки, poll — опрос, gallery — карусель фото.
VARIATIONS = ("photo", "link", "article", "text", "poll", "gallery")

# Форматы контента, которые диктуют конкретную вариацию доставки.
# Остальные форматы (facts, news, when, quote, behind_scenes, announcement,
# riddle) идут через случайный выбор pick_variation по весам post_variations.
FORMAT_TO_KIND: dict[str, str] = {
    "video": "link",         # video_url → пост-ссылка с YouTube-превью
    "infographic": "photo",  # инфографика рендерится как картинка
    "collage": "gallery",    # обзор с несколькими картинками
    "interview": "article",  # диалог уходит статьёй
    "poll": "poll",          # опрос
    "trailer": "link",       # трейлер → ссылка с превью видео
    "review": "article",     # рецензия → статья
    "comparison": "gallery", # сравнение → карусель (2 картинки)
    "opinion": "article",    # аналитика → статья
}


@dataclass
class VariationPlan:
    """Всё, что нужно для публикации поста выбранной вариацией."""

    kind: str
    requested_kind: str
    image: ImageResult
    link_url: str = ""
    extra_images: list[str] = field(default_factory=list)
    poll_question: str = ""
    poll_options: list[str] = field(default_factory=list)
    include_facts: bool = False


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


def _valid_url(raw: Any) -> str:
    """http(s)-ссылка или ''. LLM может вернуть «см. в YouTube» вместо URL."""
    text = str(raw or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return ""


def _pick_image(
    choice: Choice,
    final_data: dict,
    wiki_title: str,
    cfg: Config,
    deadline: Deadline | None = None,
) -> ImageResult:
    """Картинка поста: сначала иллюстрация из самого фида, потом Википедия.

    media:content/enclosure — это кадр конкретной статьи: он не требует
    запроса к wiki и не может «уехать» на актёра вместо фильма. Википедия —
    фолбэк, когда фид картинку не отдал.
    """
    feed = [_valid_url(u) for u in choice.item.images]
    feed = [u for u in feed if u]
    if feed:
        return ImageResult(url=feed[0], title="иллюстрация источника", lang="feed")
    if deadline is None or check(deadline, step="картинка"):
        return find_image_multi(
            [
                wiki_title,
                str(final_data.get("film", "")).strip(),
                str(final_data.get("director", "")).strip(),
            ],
            cfg,
        )
    logger.warning("бюджет исчерпан, картинку пропускаем")
    return ImageResult()


def _gallery_images(
    wiki_title: str,
    final_data: dict,
    choice: Choice,
    main_url: str,
    cfg: Config,
    candidates: list[str] | None = None,
) -> list[str]:
    """Картинки карусели: кандидаты из черновика → wiki → иллюстрации фида.

    Дедуп по URL обязателен: одна и та же картинка в sendMediaGroup дважды
    не пройдёт, а первая уже ушла как main.
    """
    urls: list[str] = []

    def add(url: str) -> None:
        if url and url != main_url and url not in urls:
            urls.append(url)

    if candidates:
        for url in find_images(wiki_title, candidates, cfg):
            add(url)
    if len(urls) < 2:
        for url in find_images(
            wiki_title,
            [str(final_data.get("film", "")), str(final_data.get("director", ""))],
            cfg,
        ):
            add(url)
    if len(urls) < 2:
        for url in choice.item.images:
            add(_valid_url(url))
    return urls[:4]


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
        # Форматные поля (дата анонса, вердикт рецензии, строки сравнения,
        # подсказка загадки) и цифры инфографики без графика — в тело статьи.
        # Без этого review/announcement/comparison уходили в graph.org голым
        # текстом: structured-данные LLM генерировало, но не показывало.
        body_parts = [
            md_bold_to_html(str(draft_data.get("text", "")).strip()),
            *format_fields_plain(draft_data),
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
    except Exception as exc:  # noqa: BLE001 — статья не должна ронять пост
        logger.warning("статья недоступна (%s): %s — фолбэк на фото",
                       exc.__class__.__name__, exc)
        return ""


def pick_variation(
    cfg: Config,
    draft_data: dict,
    image_url: str,
    choice: Choice,
) -> tuple[str, str]:
    """Выбирает вариацию поста. Возвращает (kind, link_url).

    Диктуют вариацию 9 форматов (video/trailer→link, infographic→photo,
    collage/comparison→gallery, interview/review/opinion→article, poll→poll);
    остальные 7 идут через случайный выбор по весам post_variations.

    Ссылка для link — всегда настоящая: сначала видео из фида (реальный
    embed), потом video_url из черновика (если это URL, а не «посмотри на
    YouTube»), потом ссылка новости. Выдуманной ссылке тут делать нечего.
    """
    forced_kind = FORMAT_TO_KIND.get(choice.format, "")

    if forced_kind:
        if forced_kind == "link":
            url = (
                _valid_url(choice.item.video_url)
                or _valid_url(draft_data.get("video_url"))
                or _valid_url(choice.item.link)
            )
            if url:
                logger.info("вариация продиктована форматом %s: link (%s)",
                            choice.format, "видео из фида" if _valid_url(choice.item.video_url) == url else "ссылка")
                return "link", url
            logger.warning("формат %s без ссылки — фолбэк на photo", choice.format)
            return "photo", ""
        logger.info("вариация продиктована форматом %s: %s", choice.format, forced_kind)
        return forced_kind, ""

    weights_raw = getattr(cfg.settings, "post_variations", {}) or {}
    if isinstance(weights_raw, dict):
        unknown = sorted(set(weights_raw) - set(VARIATIONS))
        if unknown:
            logger.warning("post_variations: неизвестные ключи пропущены: %s",
                           ", ".join(unknown))
    else:
        logger.warning("post_variations не словарь (%s), берём photo",
                       type(weights_raw).__name__)
        weights_raw = {}

    weights = [_safe_int(weights_raw.get(v, 0)) for v in VARIATIONS]
    if not any(weights):
        weights = [1, 0, 0, 0, 0, 0]  # не настроено — только фото, как раньше
    kind = random.choices(VARIATIONS, weights=weights, k=1)[0]

    if kind == "link":
        # Превью YouTube из фида играется прямо в посте — это штатный
        # способ показать видео (сам YouTube мы не трогаем).
        url = _valid_url(choice.item.video_url) or _valid_url(choice.item.link)
        if url:
            return "link", url
        logger.warning("вариация link без ссылки новости — фолбэк на photo")
        return "photo", ""
    return kind, ""


def apply_infographic(choice: Choice, final_data: dict,
                      image: ImageResult) -> tuple[ImageResult, bool]:
    """Карточка инфографики вместо фото: график QuickChart по полю infographic.

    Возвращает (image, include_facts). Если данные от LLM не собрались,
    картинка остаётся как была, а include_facts=True просит подпись напечатать
    цифры текстом: иначе пост теряет смысл формата.
    """
    if choice.format != "infographic":
        return image, False

    from kino.infographic import build_url, validate

    title, facts = validate(final_data.get("infographic"))
    if not facts:
        logger.warning("инфографика: нет данных от LLM, цифры уйдут в текст")
        return image, True
    url = build_url(title, facts)
    if not url:
        logger.warning("инфографика: URL не собрался, цифры уйдут в текст")
        return image, True
    logger.info("инфографика: %s (%d фактов)", title, len(facts))
    return ImageResult(url=url, title=title, lang="infographic"), False


def resolve_variation(
    cfg: Config,
    choice: Choice,
    final_data: dict,
    wiki_title: str,
    deadline: Deadline | None = None,
) -> VariationPlan:
    """Выбор вариации + подготовка материала + фолбэки — в одном месте.

    Возвращает VariationPlan: финальный kind (после всех превращений),
    картинку, ссылки, данные опроса и флаг include_facts для подписи.
    После этого функция kind больше не меняется — caption и publish получают
    согласованный план.
    """
    image = _pick_image(choice, final_data, wiki_title, cfg, deadline)
    if image.url:
        logger.info("картинка: %s (%s, %s)", image.title or "без названия", image.lang, image.url)
    else:
        logger.info("картинка не найдена, кандидаты: wiki=%r film=%r director=%r",
                    wiki_title, final_data.get("film"), final_data.get("director"))

    kind, link_url = pick_variation(cfg, final_data, image.url, choice)
    requested_kind = kind

    image, include_facts = apply_infographic(choice, final_data, image)

    extra_images: list[str] = []
    poll_q = ""
    poll_opts: list[str] = []

    if kind == "gallery":
        candidates: list[str] = []
        raw = final_data.get("image_candidates")
        if isinstance(raw, list):
            candidates = [str(c).strip() for c in raw if str(c).strip()]
        if len(candidates) < 2:
            # Для comparison пары берутся из структурированного поля.
            comp = final_data.get("comparison")
            if isinstance(comp, dict):
                candidates = [
                    c for c in (
                        str(comp.get("item_a", "") or "").strip(),
                        str(comp.get("item_b", "") or "").strip(),
                    ) if c
                ]
        extra_images = _gallery_images(
            wiki_title, final_data, choice, image.url, cfg,
            candidates=candidates or None,
        )
        if len(extra_images) < 2:
            logger.warning("%s: картинок на карусель не хватило", choice.format)
            kind = "photo"
    if kind == "article":
        link_url = _publish_article(cfg, final_data, image.url, choice)
        kind = "link" if link_url else "photo"
    if kind == "poll":
        poll_q, poll_opts = _build_poll(final_data)
        if not poll_q:
            kind = "photo"  # нет вопроса — фолбэк
    # Вариации с картинкой без картинки не имеют смысла: photo/gallery уходят
    # текстом. Но если для галереи нашлись extra_images, первая идёт как
    # основная — сравнение/коллаж не должны терять все картинки разом.
    if kind in ("photo", "gallery") and not image.url:
        if kind == "gallery" and extra_images:
            image = ImageResult(url=extra_images[0], title="gallery", lang="none")
            extra_images = extra_images[1:]
            logger.info("галерея: первая extra-картинка стала основной")
        else:
            logger.warning("вариация %s без картинки — уходим текстом", kind)
            kind = "text"

    if kind != requested_kind:
        if requested_kind == "article" and kind == "link":
            # article штатно превращается в link: пост ведёт на страницу
            # graph.org. Это не фолбэк, не пугаем лог словом «фолбэк».
            logger.info("вариация поста: link (статья graph.org)")
        else:
            logger.info("вариация поста: %s (выбрана %s, фолбэк)", kind, requested_kind)
    else:
        logger.info("вариация поста: %s", kind)

    return VariationPlan(
        kind=kind,
        requested_kind=requested_kind,
        image=image,
        link_url=link_url,
        extra_images=extra_images,
        poll_question=poll_q,
        poll_options=poll_opts,
        include_facts=include_facts,
    )
