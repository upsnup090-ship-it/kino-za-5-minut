"""Тесты загрузки и подстановки промптов."""

from __future__ import annotations

from kino import prompts


def test_draft_system_loads_nonempty():
    text = prompts.draft_system()
    assert isinstance(text, str)
    assert "русском" in text
    assert "JSON" in text


def test_judge_system_loads_nonempty():
    text = prompts.judge_system()
    assert isinstance(text, str)
    assert "verdict" in text
    assert "accept" in text
    assert "drop_fields" in text
    assert "review.rating" in text


def test_draft_user_renders_placeholders():
    text = prompts.draft_user(
        format="news",
        format_spec="news — оперативная новость.",
        source="Variety",
        title="Dune Part Three",
        snippet="Denis Villeneuve confirms.",
    )
    assert "news" in text
    assert "Variety" in text
    assert "Dune Part Three" in text
    assert "Denis Villeneuve confirms." in text
    assert "news — оперативная новость." in text
    # плейсхолдеров не осталось
    assert "{" not in text


def test_judge_user_renders_placeholders():
    text = prompts.judge_user(
        source="Deadline",
        title="Superman trailer",
        snippet="James Gunn unveils.",
        format="trailer",
        format_spec="trailer — новость о трейлере.",
        draft='{"headline": "x"}',
    )
    assert "Deadline" in text
    assert "Superman trailer" in text
    assert '{"headline": "x"}' in text
    assert "trailer — новость о трейлере." in text
    # плейсхолдеров не осталось (фигурные скобки в JSON черновика не считаются)
    assert "{source}" not in text
    assert "{title}" not in text
    assert "{draft}" not in text


def test_format_spec_loads_existing_file():
    text = prompts.format_spec("facts")
    assert "facts" in text
    assert "fact_source" in text


def test_format_spec_missing_file_returns_fallback_and_logs(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(prompts, "FORMATS_DIR", tmp_path)
    with caplog.at_level("ERROR"):
        text = prompts.format_spec("missingfmt")
    assert "Спецификация формата" in text
    assert "не найдена" in caplog.text


def test_format_spec_empty_file_returns_fallback_and_logs(tmp_path, monkeypatch, caplog):
    fmt_dir = tmp_path / "formats"
    fmt_dir.mkdir()
    (fmt_dir / "empty.txt").write_text("   ", encoding="utf-8")
    monkeypatch.setattr(prompts, "FORMATS_DIR", fmt_dir)
    with caplog.at_level("ERROR"):
        text = prompts.format_spec("empty")
    assert "Спецификация формата" in text
    assert "пуста" in caplog.text


def test_missing_file_falls_back_to_default(monkeypatch, tmp_path):
    monkeypatch.setattr(prompts, "PROMPTS_DIR", tmp_path)
    text = prompts.draft_system()
    assert "русском" in text  # дефолт содержит ключевое слово


def test_render_unknown_key_keeps_placeholder():
    text = prompts.render("привет {name}, {missing}", name="Арнольд")
    assert "Арнольд" in text
    assert "{missing}" in text


#: Лимит system-промпта в байтах. Критерий дизайн-дока (п.12.4): после
#: рефактора промпт сжат 12.6 КБ → ~6 КБ. Регресс 94178e0 раздул файл до
#: 7.7 КБ незаметно (ни один тест за размером не следил) — держим потолок.
_DRAFT_SYSTEM_MAX_BYTES = 6000


def test_draft_system_prompt_within_budget():
    """system-промпт не распухает: держим ≤ 6000 байт.

    Если тест упал — скорее всего в draft_system.txt снова продублировали
    состав полей, который уже есть в prompts/formats/*.txt и draft_user.txt.
    """
    size = len(prompts.draft_system().encode("utf-8"))
    assert size <= _DRAFT_SYSTEM_MAX_BYTES, (
        f"draft_system.txt распух до {size} Б (лимит {_DRAFT_SYSTEM_MAX_BYTES}). "
        "Проверь, не дублирует ли он spec-файлы prompts/formats/*.txt."
    )


def test_expected_fields_reads_spec_section():
    """Ожидаемые поля формата берём из его же спецификации (секция ПОЛЯ:)."""
    assert prompts.expected_fields("news") == ["news"]
    assert prompts.expected_fields("facts") == ["fact_source"]
    # У формата бывает несколько top-level ключей.
    assert prompts.expected_fields("collage") == ["collage", "image_candidates"]
    assert prompts.expected_fields("trailer") == ["trailer", "video_url"]


def test_expected_fields_covers_all_formats():
    formats = sorted(p.stem for p in prompts.FORMATS_DIR.glob("*.txt"))
    assert len(formats) == 16, f"spec-файлов {len(formats)}, ожидалось 16"
    for fmt in formats:
        assert prompts.expected_fields(fmt), f"{fmt}: пустой список ожидаемых полей"


def test_expected_fields_missing_file_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(prompts, "FORMATS_DIR", tmp_path)
    assert prompts.expected_fields("nope") == []