"""Проверка черновика вторым вызовом LLM."""

from __future__ import annotations

import json
import logging

import requests

from kino.choose import Choice
from kino.config import Config

logger = logging.getLogger(__name__)


def build_judge_prompt(choice: Choice, draft_data: dict, cfg: Config) -> dict:
    """Собирает тело запроса к судье."""
    item = choice.item
    system = (
        "Ты — строгий фактчекер и редактор Telegram-канала о кино. "
        "Работай только с материалом ниже. Верни строго JSON без markdown."
    )
    user = (
        f"Источник: {item.source}\n"
        f"Заголовок: {item.title}\n"
        f"Описание: {item.snippet}\n\n"
        f"Черновик: {json.dumps(draft_data, ensure_ascii=False)}\n\n"
        "Верни JSON: verdict, headline, polished_text, question, film, director, "
        "cast, release, studio, wiki, confidence."
    )
    return {
        "model": cfg.judge_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": cfg.settings.judge_temperature,
        "max_tokens": cfg.settings.judge_max_tokens,
    }


def judge(choice: Choice, draft_data: dict, cfg: Config) -> dict:
    """Проверяет черновик. При любой ошибке возвращает черновик как есть."""
    body = build_judge_prompt(choice, draft_data, cfg)
    try:
        response = requests.post(
            f"{cfg.llm_base_url.rstrip('/')}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {cfg.llm_api_key}"},
            timeout=cfg.settings.llm_timeout,
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("пустой ответ судьи")
        content = str(choices[0].get("message", {}).get("content", ""))
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("в ответе судьи нет JSON")
        parsed = json.loads(content[start : end + 1])
        if parsed.get("polished_text"):
            draft_data["text"] = parsed["polished_text"]
        if parsed.get("headline"):
            draft_data["headline"] = parsed["headline"]
        draft_data["verdict"] = parsed.get("verdict", "accept")
        return draft_data
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        logger.warning("судья недоступен (%s), берём черновик", exc.__class__.__name__)
        draft_data["verdict"] = "fallback"
        return draft_data