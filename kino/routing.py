"""Маршрутизация формата по типу материала.

Чистая логика без побочных эффектов и без обращений к сети/LLM — по образцу
`schedule.py`: данные приходят параметрами, наружу уходят множества и списки.
Подключение в `choose.py` — отдельная фаза (см. docs/source-routing-design.md).

Решаемая проблема: `choose._pick_format` выбирал формат вслепую, не глядя на
материал. Живая сессия 1 (19.10) показала исход: на новости про трейлер выпал
`facts` и вышел пересказ новости, а `quote` остался без цитаты.

Три слоя:

1. **kind** — класс материала (11 штук: news, release_date, trailer, video,
   interview, review, boxoffice, production, analysis, archive, festival).
   Определяется априором фида + маркерами в заголовке и сниппете.
2. **signal** — есть ли в материале дата / числа / спикер / видео.
3. **allowed_formats** — формат допущен, если его класс ∈ определённых И
   требования выполнены. Сигналы только допускают, ничего не запрещают.

`kinds: []` в правиле означает «любой класс» — так устроен `news`, он же фолбэк.
"""

from __future__ import annotations

import random
import re
from functools import lru_cache
from typing import Any, Iterable

#: Классы материала (таксономия утверждена 19.10, docs/source-routing-design.md §3).
KINDS: tuple[str, ...] = (
    "news", "release_date", "trailer", "video", "interview", "review",
    "boxoffice", "production", "analysis", "archive", "festival",
)

#: Требования к материалу (там же, §3.1).
SIGNALS: tuple[str, ...] = ("date", "number", "speaker", "video")

#: Априорный класс фида → базовые классы его элементов. `mixed` — пресса,
#: где решает классификатор элемента.
FEED_KIND_BASE: dict[str, tuple[str, ...]] = {
    "mixed": ("news",),
    "video": ("video",),
    "trailer": ("trailer",),
    "boxoffice": ("boxoffice",),
    "archive": ("archive",),
}

#: Маппинг формата → правила допуска (§4, утверждён владельцем 19.10).
#: Конфиг `formats.yaml: routing` может его переопределить; этот словарь —
#: каноны-фолбэк, чтобы пайплайн не упал при отсутствии секции.
DEFAULT_ROUTING: dict[str, dict[str, Any]] = {
    "fallback": "news",
    "news": {"kinds": [], "require": []},
    "when": {"kinds": ["release_date", "festival"], "require": ["date"]},
    "announcement": {"kinds": ["release_date", "news"], "require": ["date"]},
    "trailer": {"kinds": ["trailer"], "require": ["video"]},
    "video": {"kinds": ["video", "trailer"], "require": ["video"]},
    "quote": {"kinds": ["interview", "news"], "require": ["speaker"]},
    "interview": {"kinds": ["interview"], "require": []},
    "review": {"kinds": ["review"], "require": []},
    "infographic": {
        "kinds": ["boxoffice", "release_date", "festival", "news"],
        "require": ["number"],
    },
    "facts": {
        "kinds": ["archive", "production", "interview", "analysis"],
        "require": [],
    },
    "behind_scenes": {"kinds": ["production"], "require": []},
    "comparison": {"kinds": ["review", "boxoffice", "analysis"], "require": []},
    "opinion": {"kinds": ["analysis", "review"], "require": []},
    "collage": {
        "kinds": ["archive", "production", "festival", "news"],
        "require": [],
    },
    "riddle": {"kinds": ["archive", "production"], "require": []},
    "poll": {
        "kinds": ["news", "festival", "boxoffice", "release_date"],
        "require": [],
    },
}

# --- маркеры классов -----------------------------------------------------------

_KIND_MARKERS: dict[str, tuple[str, ...]] = {
    "release_date": (
        "дата премьеры", "дата выхода", "премьер*", "в прокат*", "прокат*",
        "релиз*", "release date", "release*", "premiere*", "стартует",
        "выйдет", "выйдёт", "выходит", "когда выйдет", "окно релиза",
    ),
    "trailer": (
        "трейл*", "тизер*", "teaser*", "trailer*", "first look",
        "первый взгляд", "official clip", "ролик*",
    ),
    "video": (
        "видео*", "клип*", "отрывок*", "сцена из", "кадр из", "video*",
        "clip*", "scene*", "watch", "стрим*", "трансляц*",
    ),
    "interview": (
        "интервью", "беседа", "бесед*", "поговорил*", "рассказал*",
        "заявил*", "объяснил*", "поделился", "признался", "раскрыл",
        "назвал*", "прокомментировал*", "interview*", "speaks out",
        "on why", "recalls", "opens up",
    ),
    "review": (
        "рецензи*", "обзор*", "разбор", "вердикт*", "оценк*", "критик*",
        "без спойлеров", "спойлер*", "review*", "verdict*", "rating*",
        "critic*", "grade*", "star rating",
    ),
    "boxoffice": (
        "сбор*", "кассов*", "касс*", "прокат собрал*", "уикенд*", "бюджет*",
        "окупаемост*", "box office", "box-office", "opening weekend",
        "budget*", "grosse*", "earning*",
    ),
    "production": (
        "съемк*", "закулисье*", "кастинг*", "присоединились", "павильон*",
        "спецэффект*", "дубл*", "режиссер снял*", "behind the scenes",
        "casting*", "production*", "wrapped", "filming", "reshoot*", "vfx",
    ),
    "analysis": (
        "почему", "тренд*", "рынок*", "индустри*", "стратеги*", "лонгрид*",
        "анализ*", "будущее", "проблем*", "влияни*", "что изменил*",
        "как голливуд", "эпох*", "analysis*", "why", "the future",
        "market*", "industry", "strategy", "reportedly",
    ),
    "archive": (
        "истори*", "легаси", "тогда и сейчас", "юбилей*", "годовщин*",
        "архив*", "ретроспектив*", "классик*", "лет назад", "как это было",
        "малоизвестн*", "неизвестн*", "интересные факты", "факты о",
        "legacy", "did you know", "things you didn", "anniversary",
        "retrospective", "on this day", "hidden details",
    ),
    "festival": (
        "фестивал*", "преми*", "номинац*", "наград*", "оскар*", "канн*",
        "венец*", "санденс*", "берлинале*", "эмми", "золотой глобус",
        "бафт*", "festival*", "awards", "oscar*", "cannes", "venice",
        "sundance", "berlinale", "emmy*", "golden globe", "bafta*",
        "nomination*", "nominees", "shortlist",
    ),
}

# --- маркеры требований --------------------------------------------------------

_MONTHS = (
    r"(?:январ|феврал|март|апрел|ма[йяе]|июн|июл|август|сентябр|октябр|"
    r"ноябр|декабр)[а-яё]*"
)
_DATE_RE = re.compile(
    rf"\b\d{{1,2}}\s*{_MONTHS}\b"              # 15 ноября
    rf"|\b{_MONTHS}\s+\d{{4}}\b"               # ноябрь 2026
    rf"|\b\d{{1,2}}\s+{_MONTHS}\s+\d{{2,4}}\b"  # 15 ноября 2026
    r"|\b\d{1,2}\.\d{1,2}\.\d{2,4}\b"          # 15.11.2026
    r"|\b(?:весн|лет|осен|зим)[а-яё]*\s*\d{4}\b"  # лето 2027
    r"|\bq[1-4]\s*\d{4}\b"                     # Q4 2026
    r"|\brelease date\b|\bдата премьеры\b|\bдата выхода\b",
    re.IGNORECASE,
)

_NUMBER_RE = re.compile(
    r"(?:[$€£]\s*\d)"
    r"|\b\d+(?:[.,]\d+)?\s*(?:млн|млрд|трлн)\b"
    r"|\b\d+(?:[.,]\d+)?\s*(?:b|bn|m)\b"
    r"|\b\d+(?:[.,]\d+)?\s*%"
    r"|\b\d+\s*/\s*10\b|\b\d+\s+из\s+\d+\b"
    r"|\bрейтинг\s*\d|\brating\s*\d",
    re.IGNORECASE,
)

#: Глаголы речи. «confirmed/revealed/reportedly» сюда не входят: это слова
#: издателя, а не атрибуируемое высказывание — с ними `quote` допускался на
#: новости, где цитировать нечего (провал сессии 1).
_SPEECH_VERBS = (
    "заявил*", "рассказал*", "сказал*", "объяснил*", "поделился",
    "признался", "раскрыл*", "назвал*", "прокомментировал*", "подробно*",
    "said", "says", "told", "explain*", "speaks out", "opens up",
    "interview*",
)
_QUOTE_RE = re.compile(r"[«\"][^»\"]{15,}[»\"]")

_VIDEO_HOST_RE = re.compile(r"youtube\.com|youtu\.be|vimeo\.com|nicovideo", re.IGNORECASE)


def _norm(text: str) -> str:
    """К нижнему регистру, «ё» → «е», схлопываем пробелы."""
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


@lru_cache(maxsize=None)
def _phrase_pattern(phrase: str) -> re.Pattern:
    """Граница слова слева, чтобы «doc» не ловился внутри «doctor».

    Маркер может заканчиваться на `*` — тогда это префикс: «номинац*» ловит
    номинации/номинация/номинаций, «review*» — review/reviews/reviewed. Без
    префикса правая граница обязательна. Морфология русского и английские
    множественные формы иначе теряются, а недогляд в сигналах стоит дороже
    ложного срабатывания: сигналы только допускают форматы.
    """
    text = _norm(phrase)
    prefix = text.endswith("*")
    if prefix:
        text = text[:-1]
    escaped = re.escape(text).replace(r"\ ", r"\s+")
    tail = "" if prefix else r"(?![a-zа-я0-9])"
    return re.compile(rf"(?<![a-zа-я0-9]){escaped}{tail}")


def _has_any(text: str, phrases: Iterable[str]) -> bool:
    return any(_phrase_pattern(p).search(text) for p in phrases)


def _haystack(item: Any) -> str:
    """Что читаем: заголовок + сниппет (и ссылка — для видео-хостингов)."""
    return _norm(
        f"{getattr(item, 'title', '') or ''} {getattr(item, 'snippet', '') or ''}"
    )


def detect_kinds(item: Any, feed_kind: str = "mixed") -> set[str]:
    """Классы материала: априор фида + маркеры в заголовке/сниппете.

    `news` остаётся в множестве, если ничего не нашлось: материал годный, но
    «плоский» — для него допустимы только новостные форматы.
    """
    base = FEED_KIND_BASE.get(_norm(feed_kind) or "mixed", ("news",))
    text = _haystack(item)
    kinds = set(base)
    for kind, markers in _KIND_MARKERS.items():
        if _has_any(text, markers):
            kinds.add(kind)
    # Видео из самого фида — это material-класс, а не только сигнал.
    if getattr(item, "video_url", "") or _VIDEO_HOST_RE.search(
        getattr(item, "link", "") or ""
    ):
        kinds.update({"video", "trailer"})
    return kinds


def detect_signals(item: Any) -> set[str]:
    """Есть ли в материале дата / числа / спикер / видео.

    `speaker`: прямая речь в кавычках (заголовок или сниппет) ИЛИ глагол речи
    в заголовке. В сниппете глагол не считаем — там чаще пересказ издателя,
    а для формата `quote` нужна цитируемая фраза, иначе модель её выдумает.
    """
    text = _haystack(item)
    title = _norm(getattr(item, "title", "") or "")
    signals: set[str] = set()
    if _DATE_RE.search(text):
        signals.add("date")
    if _NUMBER_RE.search(text):
        signals.add("number")
    if _QUOTE_RE.search(text) or _has_any(title, _SPEECH_VERBS):
        signals.add("speaker")
    if getattr(item, "video_url", "") or _VIDEO_HOST_RE.search(
        f"{getattr(item, 'link', '') or ''} {text}"
    ):
        signals.add("video")
    return signals


def allowed_formats(
    kinds: set[str],
    signals: set[str],
    routing: dict[str, Any] | None = None,
) -> list[str]:
    """Форматы, допущенные для этих классов и требований.

    Порядок — как в правиле (стабильно для тестов). Пустое `kinds` в правиле
    значит «любой класс». Если не допущен ничего — возвращаем [fallback].
    """
    rule_set = routing or DEFAULT_ROUTING
    fallback = str(rule_set.get("fallback", "news"))
    allowed: list[str] = []
    for fmt, rule in rule_set.items():
        if fmt == "fallback" or not isinstance(rule, dict):
            continue
        need_kinds = rule.get("kinds") or []
        need_signals = rule.get("require") or []
        if need_kinds and not (set(need_kinds) & kinds):
            continue
        if not set(need_signals) <= signals:
            continue
        allowed.append(fmt)
    return allowed or [fallback]


def pick_format(
    weights: dict[str, int],
    allowed: list[str],
    *,
    random_func=random.random,
) -> str:
    """Взвешенный выбор внутри допущенных форматов (веса ренормализуются).

    Недопущенный формат не может выпасть в принципе, даже с большим весом.
    Если пересечения весов и допущенных нет — отдаём первый допущенный.
    """
    pool = {fmt: weights.get(fmt, 0) for fmt in allowed}
    pool = {fmt: weight for fmt, weight in pool.items() if weight > 0}
    if not pool:
        return allowed[0] if allowed else ""
    total = sum(pool.values())
    roll = random_func() * total
    for fmt, weight in pool.items():
        roll -= weight
        if roll <= 0:
            return fmt
    return next(reversed(pool))  # страховка от потери точности на float