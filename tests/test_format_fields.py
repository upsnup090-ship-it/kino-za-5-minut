"""Контракт формат-полей: валидность примеров и лимиты."""

from __future__ import annotations

import json
from pathlib import Path

from kino.jsonparse import balanced_object

ROOT = Path(__file__).resolve().parents[1]
FORMATS_DIR = ROOT / "prompts" / "formats"


def _example_json(text: str) -> str:
    """Извлекает JSON-объект из секции ПРИМЕР.

    Пример бывает многострочным и с вложенными объектами, поэтому берём первый
    сбалансированный объект после «ПРИМЕР:» (тот же приём, что в
    kino.jsonparse.balanced_object). Прежняя построчная версия обрывала пример
    на первой строке, кончающейся «}», и ломала collage.txt с вложенным
    объектом: извлекался обрезанный кусок и тест падал на валидном примере.
    """
    marker = "ПРИМЕР:"
    idx = text.find(marker)
    if idx < 0:
        return ""
    rest = text[idx + len(marker):]
    start = rest.find("{")
    if start < 0:
        # пример-заглушка без JSON — не ошибка
        return ""
    return balanced_object(rest, start) or ""


def test_all_spec_examples_are_valid_json():
    for path in FORMATS_DIR.glob("*.txt"):
        text = path.read_text(encoding="utf-8")
        example = _example_json(text)
        if not example:
            continue
        try:
            parsed = json.loads(example)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{path.name}: невалидный JSON в ПРИМЕРЕ: {exc}") from exc
        assert isinstance(parsed, dict), f"{path.name}: пример не объект"


def test_facts_example_contains_fact_source():
    text = (FORMATS_DIR / "facts.txt").read_text(encoding="utf-8")
    example = json.loads(_example_json(text))
    assert "fact_source" in example
