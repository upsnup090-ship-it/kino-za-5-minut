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
        expect = _field(ann.get("what_to_expect"))
        if expect:
            blocks.append(esc(expect))

    rev = draft_data.get("review")
    if isinstance(rev, dict):
        verdict = _field(rev.get("verdict"))
        rating = _field(rev.get("rating"))
        line = " ".join(x for x in (verdict, f"({rating})" if rating else "") if x)
        if line:
            blocks.append(f"⭐ {esc(line)}")
        pros = [p for p in _ensure_list(rev.get("pros")) if p]
        cons = [c for c in _ensure_list(rev.get("cons")) if c]
        if pros:
            blocks.append("＋ " + esc(" · ".join(pros[:4])))
        if cons:
            blocks.append("－ " + esc(" · ".join(cons[:4])))

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

    rid = draft_data.get("riddle")
    if isinstance(rid, dict):
        # Ответ в подпись не берём намеренно: загадка без ответа в тексте.
        hint = _field(rid.get("hint"))
        if hint:
            blocks.append(f"💡 {esc(hint)}")

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
        if label and value:
            rows.append(f"• {esc(label)} — {esc(value)}")
        if len(rows) >= 5:
            break
    title = _field(info.get("title"))
    if rows and title:
        rows.insert(0, f"📊 {esc(title)}")
    return rows


#: Форматы, чей текст не режется редакторским капом: интервью, рецензия,
#: аналитика — длинные по замыслу, их хвост (вопрос, хэштеги) терять нельзя.
LONG_FORMATS = {"interview", "review", "opinion"}


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