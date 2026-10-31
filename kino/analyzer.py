"""Анализатор качества контента.

Считает метрики черновика (длина, структура, ключевые слова) и разбирает
вердикт судьи в плоскую обратную связь для статистики.

Про quality/sad/reasoning: судья возвращает их с тех пор, как они появились
в схеме judge_system.txt. Раньше их там не было, и analyze_judge_feedback
читал несуществующие поля — quality_score всегда выходил 0, а
generate_recommendations всегда выдавал полный негативный набор.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

#: Служебные слова, которые не несут смысла как ключевые.
#: Русский — предлоги, союзы, частицы, местоимения, вспомогательные глаголы.
#: Английский — минимальный набор под названия источников и англоязычные
#: вставки в тексте (Warner Bros., the, and). Список выверен: только слова,
#: которые действительно встречаются в постах канала. Прежний список на
#: ~200 английских слов был нагенерирован парами антонимов (unreachable,
#: inexpressible, non-assertive, disharmonious) — в живом тексте их нет,
#: и они не отсекали ничего, зато вытесняли значимые слова из топ-10.
STOP_WORDS: frozenset[str] = frozenset(
    """
    и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
    только ее мне было вот от меня еще нет о из ему даже при для без через между
    после перед около возле кроме над под ним нею ней ними ими нас вас них себя
    себе собой это этот эта эти этого этой этих этому этим этом тот та те того
    той тех тем теми который которая которое которые которого которой которых
    которым которыми свой своя свое свои своего своей своих своим своими весь вся
    всего всей всех всем всеми быть был была было были буду будешь будет будем
    будете будут есть может могут можно могу можешь можем можете нужно надо
    должен должна должно должны если чтобы когда где куда откуда почему зачем
    какой какая какое какие или либо также тоже лишь уже ведь вон очень более
    менее самый самая самое самые такой такая такое такие чей чья чье чьи
    the a an and or but if then than that this these those of in on at to for from
    with by as is are was were be been being have has had do does did will would
    can could shall should may might must not no nor so such very too also just
    only more most much many some any each every all both few less least own same
    other another it its he she they them their his her we us our you your i me my
    who whom whose what which when where why how there here now
    """.split()
)

#: Слова общего смысла: грамматически значимые, но бесполезные как ключевые.
#: «вышел», «новый», «фильм», «роль», «показал» есть почти в каждом посте
#: канала — в топ-10 они вытесняют названия и имена, ради которых ключевые
#: слова и считаются. Это не стоп-слова (они несут смысл), поэтому отдельный
#: набор: фильтр тот же, но список ведётся и правится независимо.
WEAK_WORDS: frozenset[str] = frozenset(
    """
    больше вторая второе второй вторые выходит вышел вышла вышли вышло главная
    главное главные главный главных заявил заявила заявили меньше много начнется
    начнутся начнётся несколько новая нового новое новой новые новый новых опять
    первая первое первые первый подтвердил подтвердила подтвердили покажет
    показал показала показали показало почти рассказал рассказала рассказали
    роли роль ролях сериал сериала сериале сериалы скоро снова сообщил
    сообщила сообщили стал стала стали стало станет теперь третий третье третьи
    третья фильм фильма фильме фильмом фильму фильмы части часть
    года году годом годы долларов миллионов миллиона миллиард миллиарда
    состоится состоятся получил получила получили собрал собрала собрали
    going says said goes went give gave take took make made get got
    know knew think thought see saw come came want wanted look looked
    """.split()
)

#: Минимальная длина значимого слова. 4 отсекает «это», «все», «год», но
#: оставляет «Дюна», «Нолан», «Канны», «Оскар» — короткие названия важны.
_MIN_WORD_LEN = 4
#: Сколько ключевых слов отдавать.
_MAX_KEYWORDS = 10


def extract_keywords(text: str) -> List[str]:
    """Ключевые слова текста: без служебных и общих, в порядке появления.

    Порядок детерминирован: раньше стоял `list(set(...))`, и порядок зависел
    от хеш-сида процесса — между запусками он мог меняться. Теперь дедуп
    через dict.fromkeys: первое вхождение задаёт позицию, повторы отсекаются.
    """
    if not text:
        return []
    # \w в Python 3 работает с юникодом: кириллица и латиница сохраняются.
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    words = cleaned.split()
    seen: dict[str, None] = {}
    for word in words:
        if len(word) < _MIN_WORD_LEN or word in STOP_WORDS or word in WEAK_WORDS:
            continue
        seen.setdefault(word, None)
        if len(seen) >= _MAX_KEYWORDS:
            break
    return list(seen)


def analyze_draft_quality(draft_data: Dict[str, Any]) -> Dict[str, Any]:
    """Метрики черновика: длина, структура, наличие блоков."""
    text = draft_data.get("text", "") or ""
    metrics: Dict[str, Any] = {
        "length": len(text),
        "has_quote": bool(draft_data.get("quote")),
        "has_video": bool(draft_data.get("video_url")),
        "has_facts": bool(draft_data.get("facts")),
        "has_review": bool(draft_data.get("review")),
        "keywords": extract_keywords(text),
        "headline_length": len(draft_data.get("headline", "") or ""),
        "has_images": bool(draft_data.get("images")),
        "word_count": len(text.split()),
        "sentence_count": len([s for s in text.split(".") if s.strip()]),
    }
    if text:
        metrics["has_links"] = bool(re.search(r"https?://", text))
        metrics["quote_count"] = len(re.findall(r'"[^"]*"', text))
        metrics["has_numbers"] = bool(re.search(r"\d+", text))
    return metrics


def analyze_judge_feedback(judge_data: Dict[str, Any]) -> Dict[str, Any]:
    """Плоская обратная связь судьи.

    Поля quality/sad/reasoning приходят от судьи по схеме judge_system.txt.
    Если судья их не вернул (fallback, обрезанный ответ), значения по
    умолчанию честно показывают отсутствие оценки: quality=None, а не 0 —
    ноль означал бы «оценено как плохое», хотя оценки не было вовсе.
    """
    quality = judge_data.get("quality")
    reasoning = judge_data.get("reasoning", "") or ""
    return {
        "quality_score": quality if isinstance(quality, int) else None,
        "is_sad": bool(judge_data.get("sad", False)),
        "has_reasoning": bool(reasoning),
        "reasoning_length": len(reasoning),
        "verdict": str(judge_data.get("verdict", "")),
    }


def evaluate_content_quality(
    draft_data: Dict[str, Any], judge_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Сводка: метрики черновика, отзыв судьи, итог и рекомендации.

    overall_quality считается только когда оценка судьи есть. Нет оценки —
    None, а не подставленный ноль: отчёт не должен показывать 0.0 как
    «качество», если судья ничего не оценил.
    """
    draft_metrics = analyze_draft_quality(draft_data)
    judge_feedback = analyze_judge_feedback(judge_data)

    score = judge_feedback["quality_score"]
    overall = None
    if score is not None:
        overall = round(score * 0.7 + min(draft_metrics["length"] / 1000, 1) * 0.3, 3)

    return {
        "draft_metrics": draft_metrics,
        "judge_feedback": judge_feedback,
        "overall_quality": overall,
        "recommendations": generate_recommendations(draft_metrics, judge_feedback),
    }


def generate_recommendations(
    draft_metrics: Dict[str, Any], judge_feedback: Dict[str, Any]
) -> List[str]:
    """Рекомендации по доработке. Срабатывают только на реальных данных.

    Раньше quality_score был всегда 0 (судья не возвращал поле), и
    «Контент низкого качества» выдавалось на каждый пост. Теперь эта
    рекомендация появляется только при настоящей низкой оценке.
    """
    recommendations: List[str] = []

    length = draft_metrics.get("length")
    if isinstance(length, int):
        if length < 100:
            recommendations.append("Текст слишком короткий, увеличьте объём информации")
        elif length > 1000:
            recommendations.append("Текст слишком длинный, упростите и сократите")

    if draft_metrics.get("has_quote") is False:
        recommendations.append("Добавьте цитату для большей убедительности")

    headline_length = draft_metrics.get("headline_length", 0)
    if (
        draft_metrics.get("has_video") is False
        and isinstance(headline_length, int)
        and headline_length > 50
    ):
        recommendations.append(
            "Добавьте видео или изображение для повышения вовлечённости"
        )

    if draft_metrics.get("has_facts") is False:
        recommendations.append("Добавьте факты и статистику для усиления доверия")

    score = judge_feedback.get("quality_score")
    if isinstance(score, int) and score < 5:
        recommendations.append("Контент низкого качества, перепроверьте информацию")

    if draft_metrics.get("has_links") is False:
        recommendations.append("Добавьте ссылки на источники для повышения достоверности")

    return recommendations