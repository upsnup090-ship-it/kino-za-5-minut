"""Черновик поста через LLM (OpenRouter / OpenAI-совместимый API)."""

from __future__ import annotations

import json
import logging

import requests

from kino.choose import Choice
from kino.config import Config

logger = logging.getLogger(__name__)


class DraftError(Exception):
    """Ошибка генерации черновика."""


def build_prompt(choice: Choice, cfg: Config) -> dict:
    """Собирает тело запроса к LLM для черновика."""
    item = choice.item
    system = (
        "Ты — редактор русскоязычного Telegram-канала о кино. "
        "Отвечай одним JSON-объектом без markdown."
    )
    user = (
        f"Формат: {choice.format}\n"
        f"Источник: {item.source}\n"
        f"Заголовок: {item.title}\n"
        f"Описание: {item.snippet}\n"
        "Верни JSON: headline, text, question, film, director, cast, release, studio, wiki."
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
    }


def parse_draft(content: str) -> dict:
    """Достаёт JSON из ответа модели."""
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        raise DraftError("в ответе модели нет JSON")
    try:
        return json.loads(content[start : end + 1])
    except json.JSONDecodeError as exc:
        raise DraftError(f"не удалось разобрать JSON: {exc}") from exc


def _post_chat(cfg: Config, body: dict) -> requests.Response:
    """POST /chat/completions с единой обработкой сетевых ошибок."""
    url = f"{cfg.llm_base_url.rstrip('/')}/chat/completions"
    try:
        return requests.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {cfg.llm_api_key}"},
            timeout=cfg.settings.llm_timeout,
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


def draft(choice: Choice, cfg: Config) -> dict:
    """Запрашивает черновик у LLM. Возвращает распарсенный JSON-словарь."""
    if not cfg.draft_model:
        raise DraftError("DRAFT_MODEL не задан (ни в .env, ни в settings.yaml: models.draft)")

    body = build_prompt(choice, cfg)
    response = _post_chat(cfg, body)
    _raise_for_api_error(cfg, response)

    try:
        data = response.json()
    except ValueError as exc:
        raise DraftError("LLM вернул не-JSON ответ") from exc

    choices = data.get("choices") or []
    if not choices:
        raise DraftError("LLM вернул пустой ответ")
    content = str(choices[0].get("message", {}).get("content", ""))
    return parse_draft(content)