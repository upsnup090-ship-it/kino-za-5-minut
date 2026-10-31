"""Анализатор качества контента."""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


def extract_keywords(text: str) -> List[str]:
    """Извлечение ключевых слов из текста."""
    # Убираем знаки препинания и приводим к нижнему регистру
    cleaned = re.sub(r'[^\w\s]', '', text.lower())
    words = cleaned.split()
    
    # Убираем слишком короткие слова и распространенные слова
    stop_words = {
        'и', 'в', 'во', 'не', 'что', 'он', 'на', 'я', 'с', 'со', 'как', 'а', 'то', 
        'все', 'она', 'так', 'его', 'но', 'да', 'ты', 'к', 'у', 'же', 'вы', 'за', 
        'бы', 'по', 'только', 'ее', 'мне', 'было', 'вот', 'от', 'меня', 'еще', 
        'нет', 'о', 'из', 'ему', 'даже', 'them', 'we', 'they', 'you', 'he', 'she',
        'it', 'this', 'that', 'these', 'those', 'my', 'your', 'his', 'her', 'its',
        'our', 'their', 'me', 'him', 'us', 'them', 'who', 'what', 'when', 'where',
        'why', 'how', 'which', 'whose', 'whom', 'if', 'then', 'else', 'than', 
        'because', 'since', 'although', 'though', 'while', 'before', 'after',
        'during', 'until', 'unless', 'whether', 'as', 'like', 'such', 'same',
        'very', 'quite', 'rather', 'pretty', 'really', 'just', 'only', 'merely',
        'simply', 'obviously', 'clearly', 'certainly', 'definitely', 'absolutely',
        'completely', 'entirely', 'totally', 'fully', 'whole', 'all', 'both',
        'either', 'neither', 'each', 'every', 'any', 'some', 'many', 'much',
        'more', 'most', 'few', 'little', 'several', 'various', 'different',
        'similar', 'same', 'other', 'another', 'such', 'so', 'too', 'enough',
        'enough', 'sufficient', 'adequate', 'proper', 'appropriate', 'suitable',
        'necessary', 'essential', 'important', 'significant', 'major', 'minor',
        'primary', 'secondary', 'tertiary', 'main', 'principal', 'leading',
        'dominant', 'predominant', 'prevailing', 'domestic', 'international',
        'national', 'local', 'regional', 'global', 'worldwide', 'universal',
        'specific', 'particular', 'general', 'broad', 'wide', 'narrow', 'deep',
        'shallow', 'high', 'low', 'long', 'short', 'big', 'small', 'large',
        'huge', 'massive', 'enormous', 'gigantic', 'tiny', 'minute', 'small',
        'little', 'slight', 'negligible', 'insignificant', 'trivial', 'petty',
        'mean', 'average', 'median', 'mode', 'standard', 'typical', 'normal',
        'usual', 'common', 'frequent', 'regular', 'occasional', 'rare', 'unusual',
        'extraordinary', 'remarkable', 'exceptional', 'special', 'unique',
        'distinctive', 'characteristic', 'peculiar', 'odd', 'strange', 'weird',
        'bizarre', 'curious', 'interesting', 'fascinating', 'amazing', 'incredible',
        'astonishing', 'surprising', 'unexpected', 'unforeseen', 'uncalled',
        'unplanned', 'accidental', 'fortuitous', 'coincidental', 'casual',
        'random', 'haphazard', 'sporadic', 'periodic', 'intermittent', 'continuous',
        'constant', 'steady', 'stable', 'unstable', 'volatile', 'dynamic',
        'static', 'mobile', 'immobile', 'active', 'inactive', 'productive',
        'unproductive', 'efficient', 'inefficient', 'effective', 'ineffective',
        'successful', 'unsuccessful', 'favorable', 'unfavorable', 'positive',
        'negative', 'beneficial', 'harmful', 'advantageous', 'disadvantageous',
        'profitable', 'unprofitable', 'valuable', 'worthless', 'expensive',
        'cheap', 'affordable', 'inexpensive', 'costly', 'pricey', 'reasonable',
        'unreasonable', 'fair', 'unfair', 'just', 'unjust', 'equitable', 'unequitable',
        'equal', 'unequal', 'balanced', 'unbalanced', 'harmonious', 'disharmonious',
        'peaceful', 'peaceless', 'calm', 'turbulent', 'violent', 'non-violent',
        'aggressive', 'passive', 'assertive', 'non-assertive', 'confident',
        'insecure', 'self-assured', 'self-doubting', 'optimistic', 'pessimistic',
        'hopeful', 'despairing', 'motivated', 'demotivated', 'energetic',
        'tired', 'exhausted', 'fatigued', 'rested', 'refreshed', 'invigorated',
        'enlivened', 'animated', 'lively', 'vivacious', 'spirited', 'cheerful',
        'glum', 'morose', 'gloomy', 'melancholy', 'depressed', 'sad', 'joyful',
        'happy', 'pleased', 'content', 'satisfied', 'unsatisfied', 'dissatisfied',
        'frustrated', 'disappointed', 'disheartened', 'discouraged', 'dispirited',
        'disinclined', 'reluctant', 'willing', 'unwilling', 'ready', 'unready',
        'prepared', 'unprepared', 'expectant', 'anticipatory', 'hopeful', 'anxious',
        'worried', 'concerned', 'troubled', 'distressed', 'aggrieved', 'offended',
        'insulted', 'hurt', 'damaged', 'injured', 'harmed', 'affected', 'influenced',
        'impacted', 'changed', 'transformed', 'modified', 'altered', 'adjusted',
        'adapted', 'accommodated', 'modified', 'revised', 'amended', 'corrected',
        'fixed', 'rectified', 'repaired', 'restored', 'recovered', 'healed',
        'cured', 'treated', 'addressed', 'handled', 'managed', 'controlled',
        'regulated', 'governed', 'directed', 'guided', 'led', 'directed', 'steered',
        'pointed', 'aimed', 'targeted', 'focused', 'concentrated', 'attentive',
        'careful', 'cautious', 'prudent', 'wise', 'foolish', 'silly', 'stupid',
        'clever', 'smart', 'intelligent', 'bright', 'brilliant', 'genius', 'talented',
        'gifted', 'skilled', 'proficient', 'expert', 'masterful', 'adept', 'capable',
        'competent', 'qualified', 'qualified', 'eligible', 'suitable', 'appropriate',
        'proper', 'fit', 'convenient', 'inconvenient', 'easy', 'difficult', 'simple',
        'complex', 'complicated', 'straightforward', 'obscure', 'ambiguous',
        'unclear', 'vague', 'undefined', 'uncertain', 'indefinite', 'indefinable',
        'specifiable', 'definable', 'describable', 'undescribable', 'expressible',
        'inexpressible', 'communicable', 'incommunicable', 'transmissible',
        'intransmissible', 'transferable', 'non-transferable', 'movable', 'immovable',
        'mobile', 'immobile', 'portable', 'non-portable', 'accessible', 'inaccessible',
        'reachable', 'unreachable', 'approachable', 'unapproachable', 'available',
        'unavailable', 'accessible', 'inaccessible', 'usable', 'unusable', 'useful',
        'useless', 'helpful', 'unhelpful', 'beneficial', 'harmful', 'constructive',
        'destructive', 'creative', 'uncreative', 'innovative', 'non-innovative',
        'original', 'unoriginal', 'authentic', 'inauthentic', 'genuine', 'fake',
        'artificial', 'natural', 'synthetic', 'organic', 'inorganic', 'biological',
        'non-biological', 'physical', 'non-physical', 'material', 'non-material',
        'substantial', 'insubstantial', 'real', 'unreal', 'actual', 'potential',
        'possible', 'impossible', 'probable', 'improbable', 'likely', 'unlikely',
        'certain', 'uncertain', 'definite', 'indefinite', 'absolute', 'relative',
        'partial', 'complete', 'incomplete', 'whole', 'fragmentary', 'partial',
        'entire', 'total', 'sum', 'aggregate', 'combined', 'united', 'separate',
        'divided', 'split', 'apart', 'together', 'joined', 'connected', 'linked',
        'associated', 'related', 'connected', 'associated', 'involved', 'included',
        'contained', 'embedded', 'incorporated', 'integrated', 'combined', 'united',
        'unified', 'coordinated', 'organized', 'structured', 'ordered', 'systematic',
        'methodical', 'logical', 'illogical', 'rational', 'irrational', 'reasonable',
        'unreasonable', 'sensible', 'insensible', 'practical', 'impractical',
        'feasible', 'infeasible', 'achievable', 'unachievable', 'possible', 'impossible',
        'practicable', 'unpracticable', 'doable', 'undone', 'accomplishable',
        'unaccomplishable', 'attainable', 'unattainable', 'reachable', 'unreachable'
    }
    
    # Оставляем только уникальные слова, исключая стоп-слова
    keywords = [word for word in words if len(word) > 3 and word not in stop_words]
    return list(set(keywords))[:10]  # Ограничиваем 10 ключевыми словами


def analyze_draft_quality(draft_data: Dict[str, Any]) -> Dict[str, Any]:
    """Анализ качества черновика."""
    metrics = {
        "length": len(draft_data.get("text", "")),
        "has_quote": bool(draft_data.get("quote")),
        "has_video": bool(draft_data.get("video_url")),
        "has_facts": bool(draft_data.get("facts")),
        "has_review": bool(draft_data.get("review")),
        "keywords": extract_keywords(draft_data.get("text", "")),
        "headline_length": len(draft_data.get("headline", "")),
        "has_images": bool(draft_data.get("images")),
        "word_count": len(draft_data.get("text", "").split()),
        "sentence_count": len([s for s in draft_data.get("text", "").split('.') if s.strip()])
    }
    
    # Добавляем дополнительные метрики
    text = draft_data.get("text", "")
    if text:
        # Проверяем наличие ссылок
        metrics["has_links"] = bool(re.search(r'https?://', text))
        
        # Проверяем наличие цитат
        metrics["quote_count"] = len(re.findall(r'"[^"]*"', text))
        
        # Проверяем наличие чисел
        metrics["has_numbers"] = bool(re.search(r'\d+', text))
    
    return metrics


def analyze_judge_feedback(judge_data: Dict[str, Any]) -> Dict[str, Any]:
    """Анализ обратной связи судьи."""
    feedback = {
        "quality_score": judge_data.get("quality", 0),
        "is_sad": judge_data.get("sad", False),
        "has_reasoning": bool(judge_data.get("reasoning")),
        "reasoning_length": len(judge_data.get("reasoning", "")) if judge_data.get("reasoning") else 0
    }
    
    return feedback


def evaluate_content_quality(draft_data: Dict[str, Any], judge_data: Dict[str, Any]) -> Dict[str, Any]:
    """Оценка качества контента на основе черновика и судьи."""
    draft_metrics = analyze_draft_quality(draft_data)
    judge_feedback = analyze_judge_feedback(judge_data)
    
    # Комбинируем метрики
    evaluation = {
        "draft_metrics": draft_metrics,
        "judge_feedback": judge_feedback,
        "overall_quality": (
            judge_feedback["quality_score"] * 0.7 + 
            min(draft_metrics["length"] / 1000, 1) * 0.3  # Пример веса
        ),
        "recommendations": generate_recommendations(draft_metrics, judge_feedback)
    }
    
    return evaluation


def generate_recommendations(draft_metrics: Dict[str, Any], judge_feedback: Dict[str, Any]) -> List[str]:
    """Генерация рекомендаций по улучшению контента."""
    recommendations = []
    
    # Проверка длины текста
    if "length" in draft_metrics and draft_metrics["length"] < 100:
        recommendations.append("Текст слишком короткий, увеличьте объём информации")
    elif "length" in draft_metrics and draft_metrics["length"] > 1000:
        recommendations.append("Текст слишком длинный, упростите и сократите")
    
    # Проверка наличия цитат
    if "has_quote" in draft_metrics and not draft_metrics["has_quote"]:
        recommendations.append("Добавьте цитату для большей убедительности")
    
    # Проверка наличия видео
    if ("has_video" in draft_metrics and not draft_metrics["has_video"] and 
        "headline_length" in draft_metrics and draft_metrics["headline_length"] > 50):
        recommendations.append("Добавьте видео или изображение для повышения вовлечённости")
    
    # Проверка наличия фактов
    if "has_facts" in draft_metrics and not draft_metrics["has_facts"]:
        recommendations.append("Добавьте факты и статистику для усиления доверия")
    
    # Проверка качества оценки
    if "quality_score" in judge_feedback and judge_feedback["quality_score"] < 5:
        recommendations.append("Контент низкого качества, перепроверьте информацию")
    
    # Проверка наличия ссылок
    if "has_links" in draft_metrics and not draft_metrics["has_links"]:
        recommendations.append("Добавьте ссылки на источники для повышения достоверности")
    
    return recommendations