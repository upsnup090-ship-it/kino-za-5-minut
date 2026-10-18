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


def test_draft_user_renders_placeholders():
    text = prompts.draft_user(
        format="news",
        source="Variety",
        title="Dune Part Three",
        snippet="Denis Villeneuve confirms.",
    )
    assert "news" in text
    assert "Variety" in text
    assert "Dune Part Three" in text
    assert "Denis Villeneuve confirms." in text
    # плейсхолдеров не осталось
    assert "{" not in text


def test_judge_user_renders_placeholders():
    text = prompts.judge_user(
        source="Deadline",
        title="Superman trailer",
        snippet="James Gunn unveils.",
        draft='{"headline": "x"}',
    )
    assert "Deadline" in text
    assert "Superman trailer" in text
    assert '{"headline": "x"}' in text
    # плейсхолдеров не осталось (фигурные скобки в JSON черновика не считаются)
    assert "{source}" not in text
    assert "{title}" not in text
    assert "{draft}" not in text


def test_missing_file_falls_back_to_default(monkeypatch, tmp_path):
    monkeypatch.setattr(prompts, "PROMPTS_DIR", tmp_path)
    text = prompts.draft_system()
    assert "русском" in text  # дефолт содержит ключевое слово


def test_render_unknown_key_keeps_placeholder():
    text = prompts.render("привет {name}, {missing}", name="Арнольд")
    assert "Арнольд" in text
    assert "{missing}" in text