"""Черновик поста через LLM (OpenRouter / OpenAI-совместимый API)."""

from __future__ import annotations

import json
import logging
import time

import requests

from kino.choose import Choice
from kino.config import Config
from kino.constants import BUDGET_CEILING, BUDGET_MIN_USEFUL
from kino.deadline import Deadline
from kino.jsonparse import parse_json_object
from kino.prompts import draft_system, draft_user, format_spec

logger = logging.getLogger(__name__)

# Пауза между попытками лестницы: сбросы соединения и лимиты провайдера
# обычно транзиентны, секунды хватает, чтобы не долбить в упор.
_RETRY_DELAY = 2.0

# Поля черновика, которые имеет смысл спасти из обрезанного ответа.
# Вложенные объекты/массивы (interview[], criteria[]) salvage не тянет —
# критичные данные дублируются в text (правило 9 draft_system).
_DRAFT_SALVAGE_KEYS = (
    "headline", "text", "key_moment", "question", "hashtags",
    "video_url", "poll_options", "infographic", "image_candidates",
    "announcement", "review", "comparison", "riddle",
    "fact_source", "news", "quote", "trailer", "video", "collage",
    "production", "interview", "opinion",
)


class DraftError(Exception):
    """Ошибка генерации черновика."""


#: Базовый бюджет черновика по группам форматов. Короткие «выжимки» дают
#: 2–3 абзаца и один блок, длинные (LONG_FORMATS) — 3–4 абзаца и 5–6 блоков,
#: средние — между ними. Один общий лимит либо резал длинные, либо зря
#: раздувал короткие. Неизвестный формат берёт llm.draft.max_tokens.
_FORMAT_BUDGETS: dict[str, int] = {
    # короткие: 2-3 абзаца + один блок
    "facts": 4096, "news": 4096, "when": 4096, "quote": 4096,
    "poll": 4096, "trailer": 4096, "video": 4096,
    "announcement": 4096, "riddle": 4096,
    # средние: 3-5 структурированных элементов
    "infographic": 6144, "collage": 6144, "comparison": 6144,
    "behind_scenes": 6144,
    # длинные: 3-4 абзаца + 5-6 блоков
    "interview": 8192, "review": 8192, "opinion": 8192,
}


def format_budget(fmt: str, base: int) -> int:
    """Базовый бюджет черновика для формата. Неизвестный — общий base."""
    return _FORMAT_BUDGETS.get(fmt, base)


def build_prompt(choice: Choice, cfg: Config) -> dict:
    """Собирает тело запроса к LLM для черновика."""
    item = choice.item
    system = draft_system()
    user = draft_user(
        format=choice.format,
        format_spec=format_spec(choice.format),
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
    """POST /chat/completions с единой обработкой сетевых ошибок.

    Сбросы соединения (RemoteDisconnected и соседи) — транзиентные ошибки
    провайдера: боевой кейс 18.10, apimart рвал соединение без ответа.
    Ретраим с паузой 2/4/8с; дедлайн прогона ограничивает суммарное ожидание.
    Таймаут не ретраим — модель реально думает долго, повтор с тем же
    запросом вернёт тот же результат.
    """
    url = f"{cfg.llm_base_url.rstrip('/')}/chat/completions"
    proxies = {"http": cfg.llm_proxy_url, "https": cfg.llm_proxy_url} if cfg.llm_proxy_url else None
    base_timeout = cfg.settings.llm_timeout
    delays = (2.0, 4.0, 8.0)
    for attempt in range(len(delays) + 1):
        timeout = base_timeout
        if deadline is not None:
            if deadline.expired:
                raise DraftError("бюджет прогона исчерпан до запроса к LLM")
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
            raise DraftError(f"таймаут LLM ({base_timeout}с)") from exc
        except requests.ConnectionError as exc:
            if attempt >= len(delays) or (deadline is not None and deadline.expired):
                raise DraftError(
                    f"нет соединения с LLM ({url}): {exc.__class__.__name__}"
                ) from exc
            wait = delays[attempt]
            logger.warning("LLM: соединение сброшено (%s), повтор через %.0fс",
                           exc.__class__.__name__, wait)
            time.sleep(wait)
        except requests.RequestException as exc:
            raise DraftError(f"ошибка запроса к LLM: {exc.__class__.__name__}") from exc
    raise DraftError(f"нет соединения с LLM ({url})")  # недостижимо, страховка


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


def _request_draft(
    cfg: Config, body: dict, *, deadline: Deadline | None = None
) -> tuple[dict | None, str, str, bool]:
    """Один вызов LLM за черновиком.

    Возвращает (parsed, content, error, truncated). Ошибка не бросается:
    вызывающий решает, повторять ли с большим бюджетом.

    truncated=True означает finish_reason="length": ответ оборвался по
    max_tokens. Даже если salvage что-то вытащил (parsed не None), это
    НЕ полноценный черновик — вызывающий обязан попробовать больший бюджет.
    """
    try:
        response = _post_chat(cfg, body, deadline=deadline)
    except DraftError as exc:
        # Сетевые ошибки идут через лестницу наравне с остальными сбоями:
        # повтор со следующим бюджетом, в конце — честная ошибка наружу.
        # API-ошибки (401/429/model_not_found) идут мимо лестницы — их повтор
        # с тем же запросом бессмыслен.
        return None, "", str(exc), False
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
    truncated = finish_reason == "length"

    if truncated and not content.strip():
        # Модель израсходовала весь бюджет (reasoning-модель — на
        # reasoning_content) и до JSON не дошла: content пустой, спасать нечего.
        return None, content, (
            f"бюджет съеден reasoning (max_tokens={body.get('max_tokens')}, "
            f"reasoning={len(reasoning)} символов)"
        ), True

    if truncated:
        logger.warning(
            "черновик обрезан по max_tokens (%s) — ответ неполный",
            body.get("max_tokens"),
        )

    try:
        return parse_draft(content), content, "", truncated
    except DraftError as exc:
        return None, content, str(exc), truncated


def draft(choice: Choice, cfg: Config, *, deadline: Deadline | None = None) -> dict:
    """Запрашивает черновик у LLM. Возвращает распарсенный JSON-словарь.

    Базовый бюджет зависит от формата (_FORMAT_BUDGETS): короткие «выжимки»
    дешевле, длинные (interview/review/opinion) получают больше токенов.

    Обрезанный по max_tokens ответ (finish_reason="length") НЕ считается
    готовым черновиком, даже если salvage вытащил часть полей: в таком ответе
    фразы рвутся посередине («Вторая причина глуб...»). Поэтому лестница
    повторяет запрос с большим бюджетом, и только если все ступени обрезаны —
    берётся самый полный спасённый вариант (с предупреждением в лог).

    Дедлайн ограничивает таймаут каждого запроса и обрывает лестницу, если
    бюджет на исходе — не начинаем следующий повтор без времени.
    """
    if not cfg.draft_model:
        raise DraftError("DRAFT_MODEL не задан (ни в .env, ни в settings.yaml: models.draft)")

    base = format_budget(choice.format, cfg.settings.draft_max_tokens)
    budgets = _budget_ladder(base)
    last_error = ""
    #: Лучший спасённый результат из обрезанных ответов: (длина content, parsed).
    best_truncated: tuple[int, dict] | None = None
    for attempt, budget in enumerate(budgets):
        logger.info("черновик: запрос к %s, формат %s (попытка %d/%d, max_tokens=%d)",
                    cfg.draft_model, choice.format, attempt + 1, len(budgets), budget)
        if deadline is not None and deadline.expired:
            raise DraftError(
                f"бюджет прогона исчерпан на черновике (попытка {attempt + 1}/{len(budgets)})"
            )
        body = build_prompt(choice, cfg)
        body["max_tokens"] = budget
        parsed, content, error, truncated = _request_draft(cfg, body, deadline=deadline)
        if parsed is not None and not truncated:
            if attempt:
                logger.info("черновик получен на попытке %d (max_tokens=%d)", attempt + 1, budget)
            return parsed
        if parsed is not None and truncated:
            # Ответ оборвался, но часть полей спасена. Запоминаем как запасной
            # вариант и продолжаем лестницу: целый черновик лучше обрывков.
            if best_truncated is None or len(content) > best_truncated[0]:
                best_truncated = (len(content), parsed)
            error = f"ответ обрезан по max_tokens={budget}"
        last_error = error or last_error
        if attempt < len(budgets) - 1:
            logger.warning(
                "черновик не вышел при max_tokens=%d (%s), повтор с %d",
                budget,
                error,
                budgets[attempt + 1],
            )
            if deadline is not None and not deadline.expired:
                time.sleep(_RETRY_DELAY)

    if best_truncated is not None:
        # Все ступени обрезаны. Отдаём самый полный спасённый вариант, но
        # громко предупреждаем: в посте могут быть оборванные фразы.
        logger.warning(
            "черновик отдан из обрезанного ответа (все ступени упёрлись в лимит): "
            "возможны рваные фразы, подними llm.draft.max_tokens или бюджет формата %s",
            choice.format,
        )
        return best_truncated[1]

    raise DraftError(
        f"черновик не сгенерирован: {last_error}. "
        "Подними llm.draft.max_tokens или возьми не-reasoning модель в DRAFT_MODEL."
    )





def _budget_ladder(base: int) -> list[int]:
    """Лестница бюджетов для повторов: базовый, затем с запасом.

    Возвращает уникальные значения по возрастанию, не больше трёх попыток.
    Верхняя ступень — не меньше потолка: если базовый бюджет не вместил ответ,
    нужен заметный запас, а не удвоение малого лимита (600→1200→1800 всё равно
    упиралось в стену на reasoning-модели). Некорректный базовый лимит
    заменяется дефолтом.
    """
    if base < BUDGET_MIN_USEFUL:
        logger.warning("llm.draft.max_tokens=%s слишком мало, берём %d", base, 1500)
        base = 1500
    ladder: list[int] = []
    for value in (base, base * 2, max(base * 3, BUDGET_CEILING)):
        if value not in ladder:
            ladder.append(value)
    return ladder[:3]