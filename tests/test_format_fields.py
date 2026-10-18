"""Контракт формат-полей: валидность примеров и лимиты."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMATS_DIR = ROOT / "prompts" / "formats"


def _example_line(text: str) -> str:
    """Извлекает JSON-строку из секции ПРИМЕР."""
    in_example = False
    lines: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("ПРИМЕР:"):
            in_example = True
            # пример может быть на той же строке после двоеточия
            rest = line.split("ПРИМЕР:", 1)[1].strip()
            if rest:
                lines.append(rest)
            continue
        if in_example:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.endswith(":") and not lines:
                # пустой пример-заглушка — пропускаем
                return ""
            lines.append(stripped)
            if lines and lines[-1].endswith("}"):
                break
    return " ".join(lines)


def test_all_spec_examples_are_valid_json():
    for path in FORMATS_DIR.glob("*.txt"):
        text = path.read_text(encoding="utf-8")
        example = _example_line(text)
        if not example:
            continue
        try:
            parsed = json.loads(example)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{path.name}: невалидный JSON в ПРИМЕРЕ: {exc}") from exc
        assert isinstance(parsed, dict), f"{path.name}: пример не объект"


def test_facts_example_contains_fact_source():
    text = (FORMATS_DIR / "facts.txt").read_text(encoding="utf-8")
    example = json.loads(_example_line(text))
    assert "fact_source" in example
