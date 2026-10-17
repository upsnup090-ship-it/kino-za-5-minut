"""Черновик поста через LLM."""

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


def draft(choice: Choice, cfg: Config) -> dict:
    """Запрашивает черновик у LLM."""
    body = build_prompt(choice, cfg)
    try:
        response = requests.post(
            f"{cfg.llm_base_url.rstrip('/')}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {cfg.llm_api_key}"},
            timeout=cfg.settings.llm_timeout,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise DraftError(f"ошибка запроса к LLM: {exc.__class__.__name__}") from exc
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise DraftError("LLM вернул пустой ответ")
    content = str(choices[0].get("message", {}).get("content", ""))
    return parse_draft(content)