"""Сборка подписи поста.

Обрезка подписи — не просто срез строки: текст уже HTML-экранирован, поэтому
разрез может попасть внутрь сущности (`&amp;`) и Telegram отобьёт сообщение с
`can't parse entities`. Здесь обрезка идёт по границе блока, а хвост после неё
чистится от незакрытых сущностей и тегов.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Лимиты Telegram: подпись к фото/медиагруппе — 1024, текст сообщения — 4096.
TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_TEXT_LIMIT = 4096

# Незакрытая сущность в хвосте: &, &am, &amp, &#12, &#x1F.
_OPEN_ENTITY_RE = re.compile(r"&[a-zA-Z#0-9]*$")
# Незакрытый тег в хвосте: <b, </b, <a href="...
_OPEN_TAG_RE = re.compile(r"<[^>]*$")


def escape_html(text: str) -> str:
    """Экранирует спецсимволы HTML для parse_mode=HTML."""
    return html.escape(text, quote=False)


def _heal_tail(text: str) -> str:
    """Убирает из хвоста незакрытую HTML-сущность или тег.

    Вызывается после обрезки: срез мог разрубить `&amp;` пополам, и тогда
    Telegram получит невалидную сущность и отклонит весь пост.
    """
    healed = _OPEN_ENTITY_RE.sub("", text)
    healed = _OPEN_TAG_RE.sub("", healed)
    return healed


def _cut_at_boundary(text: str, limit: int) -> str:
    """Обрезает текст до limit, стараясь резать по границе блока или предложения.

    Порядок попыток: двойной перевод строки (граница блока в подписи) →
    конец предложения → конец строки → пробел. Если ничего не нашлось,
    режем жёстко.
    """
    if len(text) <= limit:
        return text
    window = text[:limit]
    # Граница блока: ближайший разделитель "\n\n" в последней трети.
    floor = max(0, limit // 2)
    for separator in ("\n\n", ". ", "! ", "? ", "\n", " "):
        position = window.rfind(separator, floor)
        if position > 0:
            return window[: position + len(separator)].rstrip()
    return window


def truncate_caption(text: str, max_length: int, hard_limit: int) -> str:
    """Обрезает подпись: мягкий лимит, затем жёсткий.

    Режет по границе блока, а после обрезки лечит хвост от разрубленных
    сущностей и тегов. Результат всегда валиден для parse_mode=HTML.
    """
    if len(text) <= max_length:
        return text

    budget = min(max_length, hard_limit)
    # Место под многоточие, чтобы итог не вылез за лимит.
    cut = _cut_at_boundary(text, budget - 1)
    healed = _heal_tail(cut).rstrip()

    if not healed:
        # Обрезка съела всё (текст был из одних сущностей) — не отдаём пустоту.
        logger.warning("подпись выродилась в пустую строку при обрезке")
        return ""

    if len(healed) >= len(text):
        return healed
    result = healed + "…"
    # Хвост после добавления многоточия тоже может содержать сущность.
    return _heal_tail(result) if len(result) <= budget else healed


def _ensure_list(value: Any) -> list[str]:
    """Приводит значение к списку строк.

    Берём только скаляры: None, словари и списки внутри списка — это мусор от
    модели, и в подписи он выглядел как `#None` и `#5`.
    """
    if value is None:
        return []
    if isinstance(value, list):
        items: list[str] = []
        for entry in value:
            if isinstance(entry, (str, int, float)) and not isinstance(entry, bool):
                text = str(entry).strip()
                if text:
                    items.append(text)
        return items
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value).strip()
        return [text] if text else []
    return []


def normalize_hashtags(value: Any, limit: int = 3) -> list[str]:
    """Готовит хэштеги к подписи: без `#`, пробелы → `_`, дедуп, не больше limit.

    Модель регулярно возвращает `#кино` (промпт просит без `#`) или фразу с
    пробелом — Telegram такой хэштег не распознаёт.
    """
    result: list[str] = []
    seen: set[str] = set()
    for raw in _ensure_list(value):
        tag = raw.lstrip("#").strip()
        tag = re.sub(r"\s+", "_", tag)
        tag = re.sub(r"[^\w\-_]+", "", tag, flags=re.UNICODE)
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
        if len(result) >= limit:
            break
    return result


_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def md_bold_to_html(text: str) -> str:
    """`**жирный**` → `<b>жирный</b>` для parse_mode=HTML.

    Модель учится на markdown (привычка LLM), а Telegram в HTML-режиме печатает
    звёздочки как есть. Конвертим после escape_html: звёздочки экранирование не
    трогает, а угловые скобки уже безопасны.
    """
    return _MD_BOLD_RE.sub(r"<b>\1</b>", text)


def _field(value: Any) -> str:
    """Строка из скалярного поля черновика (None/списки → '')."""
    if value is None or isinstance(value, (list, dict)):
        return ""
    return str(value).strip()


def _format_block(draft_data: dict, plain: bool = False) -> list[str]:
    """Блоки подписи из формат-специфичных полей.

    Без этого поля announcement/review/comparison/riddle, которые LLM старательно
    заполняет, не попадали в пост: build_caption читал только headline/text, и
    анонс выходил без даты, рецензия — без вердикта, сравнение — без таблицы.

    plain=True — для статьи graph.org: там html не нужен (сервис сам снимает
    теги), а escape_html показал бы читателю `&amp;`.
    """
    esc = (lambda s: s) if plain else escape_html
    blocks: list[str] = []

    # 1. announcement (используется также форматом when)
    ann = draft_data.get("announcement")
    if isinstance(ann, dict):
        parts = []
        date = _field(ann.get("date"))
        platform = _field(ann.get("platform"))
        if date:
            parts.append(f"📅 {date}")
        if platform:
            parts.append(f"📺 {platform}")
        if parts:
            blocks.append(" · ".join(parts))
        why = _field(ann.get("why_this_date"))
        if why:
            blocks.append(f"🗓 {esc(why)}")
        expect = _field(ann.get("what_to_expect"))
        if expect:
            blocks.append(esc(expect))
        quote = _field(ann.get("creator_quote"))
        if quote:
            blocks.append(f"💬 {esc(quote)}")
        backstory = _field(ann.get("backstory"))
        if backstory:
            blocks.append(f"↩️ {esc(backstory)}")

    # 2. news
    news = draft_data.get("news")
    if isinstance(news, dict):
        context = _field(news.get("context"))
        if context:
            blocks.append(f"🕐 {esc(context)}")
        whats_next = _field(news.get("whats_next"))
        if whats_next:
            blocks.append(f"➡️ {esc(whats_next)}")
        metric = _field(news.get("metric"))
        if metric:
            blocks.append(f"📈 {esc(metric)}")

    # 3. quote
    quote = draft_data.get("quote")
    if isinstance(quote, dict):
        speaker = _field(quote.get("speaker"))
        occasion = _field(quote.get("occasion"))
        date = _field(quote.get("date"))
        parts = [p for p in (speaker, occasion, date) if p]
        if parts:
            blocks.append(f"🎙 {esc(' — '.join(parts))}")

    # 4. trailer
    trailer = draft_data.get("trailer")
    if isinstance(trailer, dict):
        highlights = [h for h in _ensure_list(trailer.get("highlights")) if h]
        if highlights:
            blocks.append(f"🎬 {esc(' · '.join(highlights[:3]))}")
        release_window = _field(trailer.get("release_window"))
        if release_window:
            blocks.append(f"📅 {esc(release_window)}")

    # 5. video
    video = draft_data.get("video")
    if isinstance(video, dict):
        speakers = _field(video.get("speakers"))
        if speakers:
            blocks.append(f"👥 {esc(speakers)}")
        timecodes = video.get("timecodes")
        if isinstance(timecodes, list):
            tc_rows = 0
            for tc in timecodes:
                if not isinstance(tc, dict):
                    continue
                time = _field(tc.get("time"))
                note = _field(tc.get("note"))
                if time and note:
                    blocks.append(f"⏱ {esc(time)} — {esc(note)}")
                    tc_rows += 1
                if tc_rows >= 3:
                    break

    # 6. production (behind_the_scenes)
    production = draft_data.get("production")
    if isinstance(production, dict):
        problem = _field(production.get("problem"))
        if problem:
            blocks.append(f"🎬 {esc(problem)}")
        solution = _field(production.get("solution"))
        if solution:
            blocks.append(f"🛠 {esc(solution)}")
        numbers = _field(production.get("numbers"))
        if numbers:
            blocks.append(f"📊 {esc(numbers)}")

    # 7. interview
    interview = draft_data.get("interview")
    if isinstance(interview, list):
        pairs = 0
        for pair in interview:
            if not isinstance(pair, dict):
                continue
            q = _field(pair.get("q"))
            a = _field(pair.get("a"))
            if q and a:
                blocks.append(f"**В:** {esc(q)}\n**О:** {esc(a)}")
                pairs += 1
            if pairs >= 4:
                break

    # 8. review
    rev = draft_data.get("review")
    if isinstance(rev, dict):
        verdict = _field(rev.get("verdict"))
        rating = _field(rev.get("rating"))
        line = " ".join(x for x in (verdict, f"({rating})" if rating else "") if x)
        if line:
            blocks.append(f"⭐ {esc(line)}")
        criteria = rev.get("criteria")
        if isinstance(criteria, list):
            crit_count = 0
            for crit in criteria:
                if not isinstance(crit, dict):
                    continue
                name = _field(crit.get("name"))
                score = _field(crit.get("score"))
                if name and score:
                    blocks.append(f"▫️ {esc(name)} — {esc(score)}")
                    crit_count += 1
                if crit_count >= 5:
                    break
        pros = [p for p in _ensure_list(rev.get("pros")) if p]
        cons = [c for c in _ensure_list(rev.get("cons")) if c]
        if pros:
            blocks.append("＋ " + esc(" · ".join(pros[:4])))
        if cons:
            blocks.append("－ " + esc(" · ".join(cons[:4])))
        audience = _field(rev.get("audience"))
        if audience:
            blocks.append(f"👤 {esc(audience)}")
        similar_to = _field(rev.get("similar_to"))
        if similar_to:
            blocks.append(f"≈ {esc(similar_to)}")

    # 9. comparison
    comp = draft_data.get("comparison")
    if isinstance(comp, dict):
        a, b = _field(comp.get("item_a")), _field(comp.get("item_b"))
        if a and b:
            blocks.append(f"⚖️ {esc(a)} против {esc(b)}")
        rows: list[str] = []
        for cat in comp.get("categories") or []:
            if not isinstance(cat, dict):
                continue
            name = _field(cat.get("name"))
            winner = _field(cat.get("winner")).lower()
            detail = _field(cat.get("detail"))
            if not name:
                continue
            mark = {"a": a, "b": b}.get(winner, "")
            row = f"• {esc(name)}"
            if mark:
                row += f" — {esc(mark)}"
            if detail:
                row += f": {esc(detail)}"
            rows.append(row)
            if len(rows) >= 4:
                break
        blocks.extend(rows)
        verdict = _field(comp.get("verdict"))
        if verdict:
            blocks.append(f"🏁 {esc(verdict)}")

    # 10. collage
    collage = draft_data.get("collage")
    if isinstance(collage, dict):
        theme = _field(collage.get("theme"))
        if theme:
            blocks.append(f"🎞 {esc(theme)}")
        items = 0
        for item in collage.get("items") or []:
            if not isinstance(item, dict):
                continue
            title = _field(item.get("title"))
            note = _field(item.get("note"))
            if title and note:
                blocks.append(f"• {esc(title)} — {esc(note)}")
            elif title:
                blocks.append(f"• {esc(title)}")
            items += 1
            if items >= 4:
                break

    # 11. riddle
    rid = draft_data.get("riddle")
    if isinstance(rid, dict):
        # Ответ в подпись не берём намеренно: загадка без ответа в тексте.
        hints = _ensure_list(rid.get("hints"))
        if not hints:
            hint = _field(rid.get("hint"))
            if hint:
                hints = [hint]
        for hint in hints[:3]:
            blocks.append(f"💡 {esc(hint)}")

    # 12. opinion
    opinion = draft_data.get("opinion")
    if isinstance(opinion, dict):
        counterpoint = _field(opinion.get("counterpoint"))
        if counterpoint:
            blocks.append(f"⚖️ {esc(counterpoint)}")
        forecast = _field(opinion.get("forecast"))
        if forecast:
            blocks.append(f"🔮 {esc(forecast)}")
        takeaway = _field(opinion.get("reader_takeaway"))
        if takeaway:
            blocks.append(f"🎯 {esc(takeaway)}")

    # 13. facts
    fact_source = _field(draft_data.get("fact_source"))
    if fact_source:
        blocks.append(f"📚 {esc(fact_source)}")

    return blocks


def format_fields_plain(draft_data: dict) -> list[str]:
    """Форматные поля текстом — для тела статьи на graph.org."""
    return _format_block(draft_data, plain=True)


def _infographic_block(draft_data: dict, plain: bool = False) -> list[str]:
    """Строки с фактами, когда картинка-график не получилась."""
    esc = (lambda s: s) if plain else escape_html
    info = draft_data.get("infographic")
    if not isinstance(info, dict):
        return []
    rows: list[str] = []
    for fact in info.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        label = _field(fact.get("label"))
        value = _field(fact.get("value"))
        note = _field(fact.get("note"))
        if label and value:
            row = f"• {esc(label)} — {esc(value)}"
            if note:
                row += f" — {esc(note)}"
            rows.append(row)
        if len(rows) >= 5:
            break
    title = _field(info.get("title"))
    if rows and title:
        rows.insert(0, f"📊 {esc(title)}")
    return rows


#: Форматы, чей текст не режется редакторским капом: интервью, рецензия,
#: аналитика — длинные по замыслу, их хвост (вопрос, хэштеги) терять нельзя.
LONG_FORMATS = {"interview", "review", "opinion", "collage", "comparison", "behind_scenes"}


def build_caption(draft_data: dict, image_url: str, cfg: Any, kind: str = "",
                  include_facts: bool = False, fmt: str = "") -> str:
    """Собирает подпись в чистом журналистском стиле.

    Стиль один: заголовок, текст, форматные поля, ключевой момент, вопрос,
    хэштеги. kind нужен, чтобы не дублировать вопрос в опросе; include_facts —
    чтобы цифры инфографики остались в тексте, если график не собрался.
    """
    headline = md_bold_to_html(escape_html(str(draft_data.get("headline", "") or "").strip()))
    text = md_bold_to_html(escape_html(str(draft_data.get("text", "") or "").strip()))
    key_moment = md_bold_to_html(escape_html(str(draft_data.get("key_moment", "") or "").strip()))
    question = escape_html(str(draft_data.get("question", "") or "").strip())
    hashtags = normalize_hashtags(draft_data.get("hashtags"))

    blocks: list[str] = []

    if headline:
        blocks.append(headline)
    if text:
        blocks.append(text)
    blocks.extend(_format_block(draft_data))
    # Цифры инфографики остаются в тексте, если график не собрался
    # (include_facts) или картинки нет вовсе — иначе пост теряет смысл формата.
    if draft_data.get("infographic") and (include_facts or not image_url):
        blocks.extend(_infographic_block(draft_data))
    if key_moment:
        blocks.append(key_moment)
    if question and kind != "poll":
        blocks.append("—————")
        blocks.append(f"❓ {question}")
    if hashtags:
        blocks.append(" ".join(f"#{h}" for h in hashtags))

    caption = "\n\n".join(blocks)
    # Лимит зависит от ФОРМАТА генерации, а не только от вариации доставки
    # (решение владельца канала): длинные форматы (интервью, рецензия,
    # аналитика) не режем редакторским капом — иначе теряются хвост поста,
    # вопрос и хэштеги. Короткие «выжимки» (facts/news/when/quote и т.д.)
    # держим в стиле канала (caption_max_length) в любой вариации.
    # Жёсткий потолок в любом случае — caption_limit_for(kind): 1024 для
    # подписи к фото/карусели, 4096 для текста/ссылки/опроса.
    ceiling = caption_limit_for(kind) if kind else cfg.settings.caption_hard_limit
    if fmt in LONG_FORMATS:
        hard = soft = ceiling
    else:
        hard = min(cfg.settings.caption_hard_limit, ceiling)
        soft = min(cfg.settings.caption_max_length, hard)
    return truncate_caption(caption, max_length=soft, hard_limit=hard)


def caption_limit_for(kind: str) -> int:
    """Лимит длины подписи для вариации поста.

    Подпись к фото и медиагруппе Telegram режет на 1024 символах, текст
    сообщения — на 4096. Раньше везде применялся общий лимит 1000, из-за чего
    текстовые вариации резались без причины.
    """
    if kind in ("text", "link", "poll"):
        return TELEGRAM_TEXT_LIMIT
    return TELEGRAM_CAPTION_LIMIT