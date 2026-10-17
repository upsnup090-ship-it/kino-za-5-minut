"""Проверка черновика вторым вызовом LLM."""

from __future__ import annotations

import json
import logging

import requests

from kino.choose import Choice
from kino.config import Config
from kino.prompts import judge_system, judge_user

logger = logging.getLogger(__name__)


def build_judge_prompt(choice: Choice, draft_data: dict, cfg: Config) -> dict:
    """Собирает тело запроса к судье."""
    item = choice.item
    system = judge_system()
    user = judge_user(
        source=item.source,
        title=item.title,
        snippet=item.snippet,
        draft=json.dumps(draft_data, ensure_ascii=False),
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
    if not cfg.judge_model:
        logger.warning("JUDGE_MODEL не задан, берём черновик как есть")
        draft_data["verdict"] = "fallback"
        return draft_data
    try:
        response = requests.post(
            f"{cfg.llm_base_url.rstrip('/')}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {cfg.llm_api_key}"},
            timeout=cfg.settings.llm_timeout,
        )
        if response.status_code >= 400:
            detail = response.text[:300]
            logger.warning("судья вернул %s: %s", response.status_code, detail)
            draft_data["verdict"] = "fallback"
            return draft_data
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
        if parsed.get("text"):
            draft_data["text"] = parsed["text"]
        if parsed.get("headline"):
            draft_data["headline"] = parsed["headline"]
        # Новые поля стиля: переносим только если судья их вернул непустыми.
        for key in ("key_moment", "hashtags"):
            value = parsed.get(key)
            if value not in (None, "", []):
                draft_data[key] = value
        for key in ("film", "director", "cast", "release", "studio", "wiki"):
            value = parsed.get(key)
            if value not in (None, ""):
                draft_data[key] = value
        draft_data["verdict"] = parsed.get("verdict", "accept")
        return draft_data
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        logger.warning("судья недоступен (%s), берём черновик", exc.__class__.__name__)
        draft_data["verdict"] = "fallback"
        return draft_data