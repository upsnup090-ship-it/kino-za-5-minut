"""Тесты выбора темы и формата."""

from kino.choose import _contains_phrase, _signature, _shared


def test_contains_phrase_word_boundary():
    assert _contains_phrase("the man was shot dead", ["shot"]) is True


def test_contains_phrase_no_substring_match():
    assert _contains_phrase("a gunshot rang out", ["shot"]) is False


def test_contains_phrase_case_insensitive():
    assert _contains_phrase("DEADPOOL & WOLVERINE", ["dead"]) is False


def test_contains_phrase_deadpool_not_sad():
    assert _contains_phrase("Deadpool & Wolverine", ["dead"]) is False


def test_contains_phrase_steamboat_not_bad():
    assert _contains_phrase("Steamboat Willie", ["steam"]) is False


def test_contains_phrase_multiword():
    assert _contains_phrase("a real estate listing", ["real estate"]) is True


def test_signature_drops_stop_words():
    sig = _signature("New film trailer for Dune")
    assert "film" not in sig
    assert "trailer" not in sig
    assert "dune" in sig


def test_shared_counts_common_tokens():
    assert _shared(["dune", "part", "two"], ["dune", "two", "three"]) == 2