"""Проверка черновика вторым вызовом LLM."""

from __future__ import annotations

import json
import logging
import re

import requests

from kino.choose import Choice
from kino.config import Config
from kino.prompts import judge_system, judge_user

logger = logging.getLogger(__name__)


def _notify_fallback(cfg: Config, reason: str) -> None:
    """Сообщает админу, что пост ушёл без проверки судьёй. Ошибки глотаем."""
    try:
        from kino.publish import notify_admin

        notify_admin(cfg, f"judge fallback: {reason}")
    except Exception:  # noqa: BLE001 — уведомление не должно ронять пайплайн
        pass


_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def _strip_fences(text: str) -> str:
    """Убирает markdown-ограждение ```json ... ```, если модель его добавила."""
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _balanced_object(text: str, start: int) -> str | None:
    """Возвращает подстроку от '{' до парной '}', корректно считая вложенность
    и пропуская фигурные скобки внутри строк."""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _escape_control_chars(blob: str) -> str:
    """Экранирует сырые переводы строк/табы внутри JSON-строк.

    Модели иногда вставляют настоящий \\n внутрь значения вместо \\n-эскейпа —
    json.loads на таком падает со 'Invalid control character'."""
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in blob:
        if in_string:
            if escaped:
                escaped = False
                out.append(ch)
                continue
            if ch == "\\":
                escaped = True
                out.append(ch)
                continue
            if ch == '"':
                in_string = False
                out.append(ch)
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            out.append(ch)
            continue
        if ch == '"':
            in_string = True
        out.append(ch)
    return "".join(out)


def parse_judge_json(content: str) -> dict:
    """Достаёт JSON-объект из ответа модели.

    Стратегии по порядку: markdown-фенсы, первый сбалансированный объект,
    экранирование управляющих символов внутри строк. Бросает ValueError,
    если ни одна не сработала — вызывающий сделает фолбэк."""
    text = _strip_fences(content)
    if not text:
        raise ValueError("пустой ответ судьи")

    candidates: list[str] = []
    start = text.find("{")
    if start >= 0:
        balanced = _balanced_object(text, start)
        if balanced:
            candidates.append(balanced)
        end = text.rfind("}")
        if end > start:
            candidates.append(text[start : end + 1])

    last_error: Exception | None = None
    for blob in candidates:
        for attempt in (blob, _escape_control_chars(blob)):
            try:
                parsed = json.loads(attempt)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(parsed, dict):
                return parsed
    if last_error is not None:
        raise ValueError(f"не удалось разобрать JSON судьи: {last_error}") from last_error
    raise ValueError("в ответе судьи нет JSON")


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
        content = str(choices[0].get("message", {}).get("content", ""))
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