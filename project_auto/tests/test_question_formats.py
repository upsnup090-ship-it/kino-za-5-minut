"""question только в тех форматах, где он несёт смысл (шаг 4b раунда 16).

caption.py:581 рисует question отдельным блоком «❓ …» для любого kind !=
"poll". Значит для facts/quote/review/trailer и остальных 12 форматов
заполненный question — лишний абзац в подписи, которого в формате нет.

Правило 22 судьи снимает такой блок постфактум, но это уже потраченные
токены и надежда на модель. Здесь — на стороне черновика: промпт говорит,
для каких форматов поле заполнять.

Список QUESTION_FORMATS — единственный источник правды; тест проверяет, что
промпты и код с ним не разъехались.
"""

from __future__ import annotations

from pathlib import Path

from kino.prompts import QUESTION_FORMATS, draft_system, draft_user, expected_fields

ROOT = Path(__file__).resolve().parents[1]
FORMATS_DIR = ROOT / "prompts" / "formats"
ALL_FORMATS = {p.stem for p in FORMATS_DIR.glob("*.txt")}


def test_question_formats_are_real_formats():
    assert QUESTION_FORMATS <= ALL_FORMATS, QUESTION_FORMATS - ALL_FORMATS


def test_question_formats_is_not_everything():
    """Если поле нужно везде — запрет в промпте бессмыслен."""
    assert len(QUESTION_FORMATS) < len(ALL_FORMATS)


def test_draft_user_names_question_formats():
    """prompts/draft_user.txt перечисляет те же форматы, что QUESTION_FORMATS."""
    text = draft_user(format="facts", format_spec="spec", source="s",
                      pub_date="31 октября 2026", title="t", snippet="sn")
    for fmt in QUESTION_FORMATS:
        assert fmt in text, f"{fmt} не назван в draft_user"


def test_draft_system_names_question_formats():
    text = draft_system()
    for fmt in QUESTION_FORMATS:
        assert fmt in text, f"{fmt} не назван в draft_system"


def test_question_formats_match_the_code_that_uses_it():
    """question нужен там, где его читает код, а не где попало.

    poll — pipeline._poll_parts берёт question как текст опроса;
    riddle — вопрос загадки; news и opinion — вовлечение читателя.
    Остальные форматы question не читают.
    """
    assert QUESTION_FORMATS == {"poll", "riddle", "news", "opinion"}


def test_formats_without_question_do_not_require_it():
    """Ни один формат вне QUESTION_FORMATS не требует question в ОБЯЗАТЕЛЬНО."""
    for path in sorted(FORMATS_DIR.glob("*.txt")):
        if path.stem in QUESTION_FORMATS:
            continue
        text = path.read_text(encoding="utf-8")
        required = text.split("ОБЯЗАТЕЛЬНО:", 1)
        if len(required) < 2:
            continue
        body = required[1].split("ЗАПРЕЩЕНО:", 1)[0]
        assert "question" not in body, (
            f"{path.stem}.txt требует question, но формат не в QUESTION_FORMATS"
        )


def test_poll_format_still_requires_question():
    text = (FORMATS_DIR / "poll.txt").read_text(encoding="utf-8")
    assert "question" in text


def test_draft_system_within_budget():
    """Промпт черновика не распухает: лимит п.30 — 6000 Б на system."""
    size = len(draft_system().encode("utf-8"))
    assert size <= 6000, f"draft_system.txt {size} Б при лимите 6000"
