"""Надёжный разбор JSON из ответов LLM.

Модели отвечают по-разному: чистый JSON, JSON в markdown-ограждении, JSON с
текстом до и после, JSON с настоящими переводами строк внутри строк, JSON,
обрезанный по max_tokens. Все эти случаи разбираются здесь, чтобы вызывающий
код (draft, judge) не падал на форматировании вместо содержательной ошибки.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def strip_fences(text: str) -> str:
    """Убирает markdown-ограждение ```json ... ```, если модель его добавила."""
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def balanced_object(text: str, start: int) -> str | None:
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


def escape_control_chars(blob: str) -> str:
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


# Признак оборванной фразы в спасённом тексте: модель успела закрыть кавычку,
# но не закончила мысль. Обрезок уходит в пост как есть, если его не отсечь.
_OPEN_TAIL_RE = re.compile(r"(?:[«„\"(]|—|\s(?:и|а|но|что|как|по|на|в|с|к|из|за|от|для))\s*$")


def _trim_incomplete_tail(value: str) -> str:
    """Отсекает оборванный хвост у спасённого текста.

    Спасатель берёт значения регуляркой, поэтому в них попадает и незаконченная
    фраза вида «...где он признался, что «был, по сути…». Такой обрезок в подписи
    выглядит как ошибка публикации, поэтому режем до последнего законченного
    предложения, а если его нет — до последнего слова.
    """
    text = value.strip()
    if not text:
        return ""
    # Незакрытая кавычка/скобка — явный признак обрыва.
    if text.count("«") > text.count("»") or text.count("(") > text.count(")"):
        cut = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
        if cut > 0:
            return text[: cut + 1].strip()
        return text.rsplit(" ", 1)[0].strip() if " " in text else ""
    if _OPEN_TAIL_RE.search(text):
        cut = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
        if cut > 0:
            return text[: cut + 1].strip()
        return text.rsplit(" ", 1)[0].strip() if " " in text else ""
    return text


def salvage_partial(text: str, keys: tuple[str, ...]) -> dict | None:
    """Вытаскивает поля из обрезанного JSON.

    Когда модель упирается в max_tokens, объект остаётся незакрытым. Полный
    разбор невозможен, но часть значений уже написана — их берём регуляркой,
    чтобы не терять результат целиком. Поддерживаются и строки, и массивы
    строк: без массива терялись все поля после `hashtags` в обрезанном ответе.
    """
    found: dict = {}
    for key in keys:
        # Строковое значение.
        match = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if match:
            raw = match.group(1)
            try:
                found[key] = json.loads(f'"{raw}"')
            except json.JSONDecodeError:
                found[key] = raw
            continue
        # Массив строк: ["a", "b", ...] — в т.ч. незакрытый.
        match = re.search(rf'"{re.escape(key)}"\s*:\s*\[([^\]]*)', text)
        if match:
            items = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1))
            decoded: list[str] = []
            for item in items:
                try:
                    decoded.append(json.loads(f'"{item}"'))
                except json.JSONDecodeError:
                    decoded.append(item)
            if decoded:
                found[key] = decoded
    return found or None


def parse_json_object(
    content: str,
    *,
    salvage_keys: tuple[str, ...] = (),
    empty_message: str = "пустой ответ модели",
    missing_message: str = "в ответе модели нет JSON",
) -> dict:
    """Достаёт JSON-объект из ответа модели.

    Стратегии по порядку: markdown-фенсы, первый сбалансированный объект,
    экранирование управляющих символов внутри строк, спасение обрезанного
    ответа. Бросает ValueError, если ни одна не сработала."""
    text = strip_fences(content)
    if not text:
        raise ValueError(empty_message)

    candidates: list[str] = []
    start = text.find("{")
    if start >= 0:
        balanced = balanced_object(text, start)
        if balanced:
            candidates.append(balanced)
        end = text.rfind("}")
        if end > start:
            candidates.append(text[start : end + 1])

    last_error: Exception | None = None
    for blob in candidates:
        for attempt in (blob, escape_control_chars(blob)):
            try:
                parsed = json.loads(attempt)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(parsed, dict):
                return parsed

    if salvage_keys:
        salvaged = salvage_partial(text, salvage_keys)
        if salvaged:
            logger.warning("ответ обрезан, спасены поля: %s", list(salvaged))
            return salvaged

    if last_error is not None:
        raise ValueError(f"не удалось разобрать JSON: {last_error}") from last_error
    raise ValueError(missing_message)