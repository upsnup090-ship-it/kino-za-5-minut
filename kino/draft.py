"""Черновик поста через LLM (OpenRouter / OpenAI-совместимый API)."""

from __future__ import annotations

import json
import logging

import requests

from kino.deadline import Deadline
from kino.choose import Choice
from kino.config import Config
from kino.jsonparse import parse_json_object
from kino.prompts import draft_system, draft_user

logger = logging.getLogger(__name__)

# Поля черновика, которые имеет смысл спасти из обрезанного ответа.
_DRAFT_SALVAGE_KEYS = (
    "headline", "text", "key_moment", "question", "hashtags",
    "video_url", "poll_options", "infographic", "image_candidates",
)


class DraftError(Exception):
    """Ошибка генерации черновика."""


def build_prompt(choice: Choice, cfg: Config) -> dict:
    """Собирает тело запроса к LLM для черновика."""
    item = choice.item
    system = draft_system()
    user = draft_user(
        format=choice.format,
        source=item.source,
        title=item.title,
        snippet=item.snippet,
    )
    return {
        "model": cfg.draft_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": cfg.settings.draft_temperature,
        "max_tokens": cfg.settings.draft_max_tokens,
        "top_p": cfg.settings.draft_top_p,
        # APIMart отдаёт SSE по умолчанию даже без stream:true — явный false
        # нужен, чтобы получить обычный JSON и не упасть на response.json().
        "stream": False,
    }


def parse_draft(content: str) -> dict:
    """Достаёт JSON из ответа модели. Обрезанный ответ спасается по ключевым
    полям, чтобы черновик не терялся из-за упора в max_tokens."""
    try:
        return parse_json_object(
            content,
            salvage_keys=_DRAFT_SALVAGE_KEYS,
            empty_message="пустой ответ модели",
            missing_message="в ответе модели нет JSON",
        )
    except ValueError as exc:
        raise DraftError(str(exc)) from exc


def _post_chat(cfg: Config, body: dict, *, deadline: Deadline | None = None) -> requests.Response:
    """POST /chat/completions с единой обработкой сетевых ошибок."""
    url = f"{cfg.llm_base_url.rstrip('/')}/chat/completions"
    proxies = {"http": cfg.llm_proxy_url, "https": cfg.llm_proxy_url} if cfg.llm_proxy_url else None
    timeout = cfg.settings.llm_timeout
    if deadline is not None:
        timeout = deadline.slice(timeout)
    try:
        return requests.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {cfg.llm_api_key}"},
            timeout=timeout,
            proxies=proxies,
        )
    except requests.Timeout as exc:
        raise DraftError(f"таймаут LLM ({cfg.settings.llm_timeout}с)") from exc
    except requests.ConnectionError as exc:
        raise DraftError(f"нет соединения с LLM: {url}") from exc
    except requests.RequestException as exc:
        raise DraftError(f"ошибка запроса к LLM: {exc.__class__.__name__}") from exc


def _raise_for_api_error(cfg: Config, response: requests.Response) -> None:
    """Разбирает HTTP-ошибки API, включая model_not_found."""
    if response.status_code < 400:
        return
    detail = response.text[:300]
    if "model_not_found" in detail:
        raise DraftError(
            f"модель '{cfg.draft_model}' не найдена на {cfg.llm_base_url}. "
            f"Проверь models.draft в settings.yaml или DRAFT_MODEL в .env. Ответ: {detail}"
        )
    if response.status_code == 401:
        raise DraftError("LLM вернул 401: неверный LLM_API_KEY")
    if response.status_code == 429:
        raise DraftError("LLM вернул 429: лимит запросов, попробуй позже")
    raise DraftError(f"LLM вернул {response.status_code}: {detail}")


def _request_draft(cfg: Config, body: dict, *, deadline: Deadline | None = None) -> tuple[dict | None, str, str]:
    """Один вызов LLM за черновиком.

    Возвращает (parsed, content, error). Ошибка не бросается: вызывающий решает,
    повторять ли с большим бюджетом.
    """
    response = _post_chat(cfg, body, deadline=deadline)
    _raise_for_api_error(cfg, response)

    try:
        data = response.json()
    except ValueError as exc:
        raise DraftError("LLM вернул не-JSON ответ") from exc

    choices = data.get("choices") or []
    if not choices:
        raise DraftError("LLM вернул пустой ответ")
    choice0 = choices[0]
    finish_reason = choice0.get("finish_reason")
    message = choice0.get("message", {}) or {}
    content = str(message.get("content", "") or "")
    reasoning = str(message.get("reasoning_content", "") or "")

    if finish_reason == "length" and not content.strip():
        # Reasoning-модель (glm-5.3-flash) израсходовала весь бюджет на
        # reasoning_content и до JSON не дошла: content пустой, спасать нечего.
        return None, content, (
            f"бюджет съеден reasoning (max_tokens={body.get('max_tokens')}, "
            f"reasoning={len(reasoning)} символов)"
        )

    if finish_reason == "length":
        logger.warning(
            "черновик обрезан по max_tokens (%s): reasoning-модель съела бюджет",
            body.get("max_tokens"),
        )

    try:
        return parse_draft(content), content, ""
    except DraftError as exc:
        return None, content, str(exc)


def draft(choice: Choice, cfg: Config, *, deadline: Deadline | None = None) -> dict:
    """Запрашивает черновик у LLM. Возвращает распарсенный JSON-словарь.

    Reasoning-модель тратит на reasoning от ~1000 до ~2000 токенов, и разброс
    непредсказуем: фиксированного лимита не хватает. Поэтому при обрезке
    пробуем ещё раз с увеличенным бюджетом, прежде чем сдаться.

    Дедлайн ограничивает таймаут каждого запроса и обрывает лестницу, если
    бюджет на исходе — не начинаем следующий повтор без времени.
    """
    if not cfg.draft_model:
        raise DraftError("DRAFT_MODEL не задан (ни в .env, ни в settings.yaml: models.draft)")

    budgets = _budget_ladder(cfg.settings.draft_max_tokens)
    last_error = ""
    for attempt, budget in enumerate(budgets):
        if deadline is not None and deadline.expired:
            raise DraftError(
                f"бюджет прогона исчерпан на черновике (попытка {attempt + 1}/{len(budgets)})"
            )
        body = build_prompt(choice, cfg)
        body["max_tokens"] = budget
        parsed, content, error = _request_draft(cfg, body, deadline=deadline)
        if parsed is not None:
            if attempt:
                logger.info("черновик получен на попытке %d (max_tokens=%d)", attempt + 1, budget)
            return parsed
        last_error = error
        if attempt < len(budgets) - 1:
            logger.warning(
                "черновик не вышел при max_tokens=%d (%s), повтор с %d",
                budget,
                error,
                budgets[attempt + 1],
            )

    raise DraftError(
        f"черновик не сгенерирован: {last_error}. "
        "Подними llm.draft.max_tokens или возьми не-reasoning модель в DRAFT_MODEL."
    )


#: Потолок бюджета: на живом API reasoning занимает 5.9k–10.5k символов
#: (1859–3210 completion-токенов), поэтому верхняя ступень должна быть
#: заметно выше базовой, иначе повторы не помогают.
_BUDGET_CEILING = 16384
#: Ниже этого порога запрос заведомо не даст ни reasoning, ни JSON.
_BUDGET_MIN_USEFUL = 512


def _budget_ladder(base: int) -> list[int]:
    """Лестница бюджетов для повторов: базовый, затем с запасом на reasoning.

    Возвращает уникальные значения по возрастанию, не больше трёх попыток.
    Верхняя ступень — не меньше потолка: замеры на живом API показали, что
    reasoning у glm-5.3-flash доходит до ~10.5k символов, и удвоения малого
    базового лимита не хватало (600→1200→1800 всё равно упиралось в стену).
    Некорректный базовый лимит заменяется дефолтом.
    """
    if base < _BUDGET_MIN_USEFUL:
        logger.warning("llm.draft.max_tokens=%s слишком мало, берём %d", base, 1500)
        base = 1500
    ladder: list[int] = []
    for value in (base, base * 2, max(base * 3, _BUDGET_CEILING)):
        if value not in ladder:
            ladder.append(value)
    return ladder[:3]