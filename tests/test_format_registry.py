"""Единый источник правды по форматам: дрейф-защита.

Ловит рассинхрон между:
- config/formats.yaml (weights)
- prompts/formats/*.txt (spec-файлы)
- kino.pipeline._FORMAT_TO_KIND (форсированные вариации)
- salvage-списки draft/judge
"""

from __future__ import annotations

from pathlib import Path

import yaml

from kino.draft import _DRAFT_SALVAGE_KEYS
from kino.judge import _SALVAGE_KEYS
from kino.pipeline import _FORMAT_TO_KIND, _VARIATIONS

ROOT = Path(__file__).resolve().parents[1]
FORMATS_DIR = ROOT / "prompts" / "formats"


def _weights() -> dict[str, int]:
    with open(ROOT / "config" / "formats.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["weights"]


def test_format_weights_match_spec_files():
    weights = _weights()
    spec_files = {p.stem for p in FORMATS_DIR.glob("*.txt")}
    assert set(weights) == spec_files, (
        f"рассинхрон: weights={set(weights)}, spec_files={spec_files}"
    )


def test_forced_formats_are_known():
    weights = _weights()
    forced = set(_FORMAT_TO_KIND)
    assert forced <= set(weights), f"неизвестные форсированные форматы: {forced - set(weights)}"


def test_forced_kind_values_are_valid_variations():
    assert set(_FORMAT_TO_KIND.values()) <= set(_VARIATIONS)


def test_free_formats_exist():
    """Есть форматы, идущие через случайный выбор — иначе pick_variation бессмыслен."""
    weights = _weights()
    free = set(weights) - set(_FORMAT_TO_KIND)
    assert free, "все форматы форсированы — случайная вариация никогда не используется"


def test_every_spec_has_six_sections():
    required = {"НАЗНАЧЕНИЕ", "СТРУКТУРА", "ОБЯЗАТЕЛЬНО", "ЗАПРЕЩЕНО", "ПОЛЯ", "ПРИМЕР"}
    for path in FORMATS_DIR.glob("*.txt"):
        text = path.read_text(encoding="utf-8")
        headers: set[str] = set()
        for line in text.splitlines():
            stripped = line.strip()
            for h in required:
                if stripped.startswith(h + ":"):
                    headers.add(h)
                    break
        missing = required - headers
        assert not missing, f"{path.name}: не хватает секций {missing}"


def test_new_format_fields_in_salvage_lists():
    new_keys = {
        "fact_source", "news", "quote", "trailer", "video", "collage",
        "production", "interview", "opinion",
    }
    for key in new_keys:
        assert key in _DRAFT_SALVAGE_KEYS, f"{key} не в _DRAFT_SALVAGE_KEYS"
        assert key in _SALVAGE_KEYS, f"{key} не в _SALVAGE_KEYS"


def test_drop_fields_in_judge_salvage():
    assert "drop_fields" in _SALVAGE_KEYS
