"""Общие фикстуры тестов."""


import pytest


@pytest.fixture(autouse=True)
def _no_llm_retry_sleep(monkeypatch):
    """Ретраи LLM не должны тормозить тесты реальными паузами 2/4/8с.

    Боевой код (kino/draft.py) спит между попытками лестницы и ретраями
    соединения — в тестах это лишние десятки секунд. Тесты, проверяющие
    сами ретраи, патчат sleep явно.
    """
    monkeypatch.setattr("kino.draft.time.sleep", lambda _s: None)
