"""Проверка черновика вторым вызовом LLM."""

from __future__ import annotations

import json
import logging
import re

import requests

from kino.choose import Choice
from kino.config import Config
from kino.deadline import Deadline
from kino.jsonparse import parse_json_object
from kino.prompts import judge_system, judge_user

logger = logging.getLogger(__name__)

#: Потолок бюджета судьи: reasoning занимает 5.9k–10.5k символов на живом API.
_BUDGET_CEILING = 16384
#: Ниже этого порога запрос заведомо не даст ни reasoning, ни JSON.
_BUDGET_MIN_USEFUL = 512


def _notify_fallback(cfg: Config, reason: str) -> None:
    """Сообщает админу, что пост ушёл без проверки судьёй. Ошибки глотаем."""
    try:
        from kino.publish import notify_admin

        notify_admin(cfg, f"judge fallback: {reason}")
    except Exception:  # noqa: BLE001 — уведомление не должно ронять пайплайн
        pass


_SALVAGE_KEYS = (
    "verdict", "headline", "text", "key_moment", "question", "hashtags", "reason",
    "video_url", "poll_options", "infographic", "image_candidates",
    "announcement", "review", "comparison", "riddle",
)


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


def _request_judge(
    cfg: Config,
    body: dict,
    *,
    deadline: Deadline | None = None,
) -> tuple[dict | None, str]:
    """Один вызов судьи. Возвращает (parsed, error).

    Ошибка не бросается наружу: вызывающий решает, пробовать ли фолбэк-модель
    или отдавать черновик как есть.
    """
    proxies = {"http": cfg.llm_proxy_url, "https": cfg.llm_proxy_url} if cfg.llm_proxy_url else None
    timeout = cfg.settings.llm_timeout
    if deadline is not None:
        timeout = deadline.slice(timeout)
    try:
        response = requests.post(
            f"{cfg.llm_base_url.rstrip('/')}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {cfg.llm_api_key}"},
            timeout=timeout,
            proxies=proxies,
        )
    except requests.RequestException as exc:
        return None, f"судья недоступен: {exc.__class__.__name__}"

    if response.status_code >= 400:
        detail = response.text[:300]
        logger.warning("судья вернул %s: %s", response.status_code, detail)
        return None, f"судья вернул HTTP {response.status_code}"

    try:
        data = response.json()
    except ValueError:
        return None, "судья вернул не-JSON"

    choices = data.get("choices") or []
    if not choices:
        return None, "пустой ответ судьи"
    choice0 = choices[0]
    finish_reason = choice0.get("finish_reason")
    message = choice0.get("message", {}) or {}
    content = str(message.get("content", "") or "")
    reasoning = str(message.get("reasoning_content", "") or "")

    if finish_reason == "length" and not content.strip():
        # Reasoning-модель съела бюджет и до JSON не дошла — спасать нечего,
        # сообщаем причину, чтобы вызывающий повторил с большим лимитом.
        return None, (
            f"бюджет судьи съеден reasoning (max_tokens={body.get('max_tokens')}, "
            f"reasoning={len(reasoning)} символов)"
        )

    if finish_reason == "length":
        logger.warning(
            "судья обрезан по max_tokens (%s) — подними llm.judge.max_tokens",
            body.get("max_tokens"),
        )
    try:
        return parse_judge_json(content), ""
    except ValueError as exc:
        logger.warning("судья вернул неразбираемый ответ (%s): %s", exc, content[:200])
        return None, f"неразбираемый ответ судьи: {exc}"


def _judge_budgets(base: int) -> list[int]:
    """Лестница бюджетов судьи: базовый, затем с запасом на reasoning.

    Та же логика, что у черновика: reasoning-модель тратит бюджет
    непредсказуемо (замеры — до ~10.5k символов), и фиксированного лимита
    не хватает. Верхняя ступень — не меньше потолка.
    """
    if base < _BUDGET_MIN_USEFUL:
        logger.warning("llm.judge.max_tokens=%s слишком мало, берём %d", base, 1500)
        base = 1500
    ladder: list[int] = []
    for value in (base, max(base * 2, _BUDGET_CEILING)):
        if value not in ladder:
            ladder.append(value)
    return ladder[:2]


def judge(
    choice: Choice,
    draft_data: dict,
    cfg: Config,
    *,
    deadline: Deadline | None = None,
) -> dict:
    """Проверяет черновик. При любой ошибке возвращает черновик как есть.

    Порядок попыток: основная модель с базовым бюджетом → она же с удвоенным
    (reasoning съел лимит) → фолбэк-модель, если задана. Только после этого
    черновик уходит без проверки.

    Дедлайн ограничивает таймаут каждого запроса и обрывает лестницу/фолбэк,
    если бюджет на исходе.
    """
    if not cfg.judge_model:
        logger.warning("JUDGE_MODEL не задан, берём черновик как есть")
        draft_data["verdict"] = "fallback"
        _notify_fallback(cfg, "JUDGE_MODEL не задан")
        return draft_data

    def _budget_ok() -> bool:
        return deadline is None or not deadline.expired

    parsed: dict | None = None
    error = ""
    for budget in _judge_budgets(cfg.settings.judge_max_tokens):
        if not _budget_ok():
            logger.warning("бюджет прогона исчерпан на судье, берём черновик")
            draft_data["verdict"] = "fallback"
            _notify_fallback(cfg, "бюджет исчерпан на судье")
            return draft_data
        body = build_judge_prompt(choice, draft_data, cfg)
        body["max_tokens"] = budget
        parsed, error = _request_judge(cfg, body, deadline=deadline)
        if parsed is not None:
            break
        logger.warning("судья не справился при max_tokens=%d (%s)", budget, error)

    if parsed is None and cfg.judge_fallback_model and _budget_ok():
        logger.warning(
            "судья %s не справился (%s), пробуем фолбэк %s",
            cfg.judge_model,
            error,
            cfg.judge_fallback_model,
        )
        fallback_body = build_judge_prompt(choice, draft_data, cfg)
        fallback_body["model"] = cfg.judge_fallback_model
        parsed, fallback_error = _request_judge(cfg, fallback_body, deadline=deadline)
        if parsed is None:
            error = f"{error}; фолбэк {cfg.judge_fallback_model}: {fallback_error}"

    if parsed is None:
        logger.warning("судья не сработал (%s), берём черновик", error)
        draft_data["verdict"] = "fallback"
        _notify_fallback(cfg, error)
        return draft_data

    try:
        if parsed.get("text"):
            draft_data["text"] = parsed["text"]
        if parsed.get("headline"):
            draft_data["headline"] = parsed["headline"]
        # Новые поля стиля: переносим только если судья их вернул непустыми —
        # иначе остаётся то, что уже было в черновике (раньше терялось).
        for key in ("key_moment", "hashtags"):
            value = parsed.get(key)
            if value not in (None, "", []):
                draft_data[key] = value
        for key in (
            "film", "director", "cast", "release", "studio", "wiki",
            "video_url", "poll_options", "infographic", "image_candidates",
            "announcement", "review", "comparison", "riddle",
        ):
            value = parsed.get(key)
            # Пустые контейнеры тоже затирание: судья часто возвращает [] или {}
            # для полей, которых нет в его описании, — реальные данные черновика
            # терялись, опрос уходил с вариантами-заглушкой, инфографика — без
            # фактов. Скаляр "" — тоже пропуск.
            if value is None or value == "" or value == [] or value == {}:
                continue
            draft_data[key] = value
        draft_data["verdict"] = parsed.get("verdict", "accept")
        if draft_data["verdict"] == "reject":
            reason = str(parsed.get("reason", "") or "").strip()
            # Причину кладём в черновик: pipeline пишет её админу в уведомлении
            # об отмене публикации. Без этого в ЛС уходило «без причины».
            draft_data["reason"] = reason
            logger.warning("судья отклонил черновик: %s", reason or "причина не указана")
        return draft_data
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("судья вернул неожиданные данные (%s), берём черновик", exc)
        draft_data["verdict"] = "fallback"
        _notify_fallback(cfg, f"неожиданные данные судьи: {exc.__class__.__name__}")
        return draft_data