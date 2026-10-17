"""Проверка черновика вторым вызовом LLM."""

from __future__ import annotations

import json
import logging
import re

import requests

from kino.choose import Choice
from kino.config import Config
from kino.jsonparse import parse_json_object
from kino.prompts import judge_system, judge_user

logger = logging.getLogger(__name__)


def _notify_fallback(cfg: Config, reason: str) -> None:
    """Сообщает админу, что пост ушёл без проверки судьёй. Ошибки глотаем."""
    try:
        from kino.publish import notify_admin

        notify_admin(cfg, f"judge fallback: {reason}")
    except Exception:  # noqa: BLE001 — уведомление не должно ронять пайплайн
        pass


_SALVAGE_KEYS = ("verdict", "headline", "text", "key_moment", "question")


def parse_judge_json(content: str) -> dict:
    """Достаёт JSON-объект из ответа судьи. Обрезанный ответ спасается по
    ключевым полям, чтобы решение судьи не терялось."""
    return parse_json_object(
        content,
        salvage_keys=_SALVAGE_KEYS,
        empty_message="пустой ответ судьи",
        missing_message="в ответе судьи нет JSON",
    )


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
        # APIMart отдаёт SSE по умолчанию даже без stream:true — явный false
        # нужен, чтобы получить обычный JSON и не упасть на response.json().
        "stream": False,
    }


def judge(choice: Choice, draft_data: dict, cfg: Config) -> dict:
    """Проверяет черновик. При любой ошибке возвращает черновик как есть."""
    body = build_judge_prompt(choice, draft_data, cfg)
    if not cfg.judge_model:
        logger.warning("JUDGE_MODEL не задан, берём черновик как есть")
        draft_data["verdict"] = "fallback"
        _notify_fallback(cfg, "JUDGE_MODEL не задан")
        return draft_data
    try:
        proxies = {"http": cfg.llm_proxy_url, "https": cfg.llm_proxy_url} if cfg.llm_proxy_url else None
        response = requests.post(
            f"{cfg.llm_base_url.rstrip('/')}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {cfg.llm_api_key}"},
            timeout=cfg.settings.llm_timeout,
            proxies=proxies,
        )
        if response.status_code >= 400:
            detail = response.text[:300]
            logger.warning("судья вернул %s: %s", response.status_code, detail)
            draft_data["verdict"] = "fallback"
            _notify_fallback(cfg, f"судья вернул HTTP {response.status_code}")
            return draft_data
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("пустой ответ судьи")
        finish_reason = choices[0].get("finish_reason")
        content = str(choices[0].get("message", {}).get("content", ""))
        if finish_reason == "length":
            logger.warning(
                "судья обрезан по max_tokens (%d) — подними llm.judge.max_tokens",
                cfg.settings.judge_max_tokens,
            )
        try:
            parsed = parse_judge_json(content)
        except ValueError as exc:
            logger.warning("судья вернул неразбираемый ответ (%s): %s", exc, content[:200])
            raise
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
        _notify_fallback(cfg, f"судья недоступен: {exc.__class__.__name__}")
        return draft_data