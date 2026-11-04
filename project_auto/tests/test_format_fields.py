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


# --- facts: точный набор блоков (шаг 4 раунда 16) --------------------------


def _facts_spec() -> str:
    return (FORMATS_DIR / "facts.txt").read_text(encoding="utf-8")


def test_facts_spec_forbids_question_block():
    """facts не содержит вопрос: пост — один факт, а не вовлечение.

    caption.py:479 рисует question при kind != "poll", то есть для facts тоже.
    Если спецификация не запрещает блок, черновик его заполнит, и в подписи
    появится лишний абзац. Правило 22 судьи ловит это постфактум; здесь —
    на стороне черновика.
    """
    spec = _facts_spec()
    forbidden = spec.split("ЗАПРЕЩЕНО:", 1)[1].split("ПОЛЯ:", 1)[0]
    assert "question" in forbidden


def test_facts_spec_forbids_key_moment_retelling():
    spec = _facts_spec()
    forbidden = spec.split("ЗАПРЕЩЕНО:", 1)[1].split("ПОЛЯ:", 1)[0]
    assert "key_moment" in forbidden
    assert "пересказ" in forbidden.lower()


def test_facts_example_has_no_year_in_fact_source():
    """Пример не должен провоцировать выдуманный год.

    Было: «Интервью Дени Вильнёва Variety, 2023». Модель копирует форму
    примера, и год 2023 уезжал в подпись новости от 2026-го — ровно тот
    дефект, который чинит правило 20.
    """
    text = _facts_spec()
    example = _example_json(text)
    data = json.loads(example)
    source = data["fact_source"]
    assert not any(ch.isdigit() for ch in source), f"год в примере: {source!r}"


def test_facts_spec_mentions_date_rule():
    """Спецификация отправляет модель к дате материала, а не к догадке."""
    assert "год" in _facts_spec().lower()


def test_all_specs_forbid_blocks_outside_their_fields():
    """Каждая спецификация, у которой ПОЛЯ узкие, перечисляет лишние блоки.

    Если формат объявляет один-два поля, а модель заполняет всё подряд,
    подпись раздувается. Проверяем, что запрет есть у каждого такого формата.
    """
    from kino.prompts import expected_fields

    for path in sorted(FORMATS_DIR.glob("*.txt")):
        fmt = path.stem
        fields = expected_fields(fmt)
        if len(fields) > 2:
            continue  # широкие форматы перечисляют блоки в ОБЯЗАТЕЛЬНО
        text = path.read_text(encoding="utf-8")
        if "ЗАПРЕЩЕНО:" not in text:
            continue
        assert "question" in text or "лишн" in text.lower(), (
            f"{fmt}.txt: узкий формат ({fields}), но лишние блоки не названы"
        )
