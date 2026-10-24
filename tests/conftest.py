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


@pytest.fixture(autouse=True)
def _isolated_stats(tmp_path, monkeypatch):
    """Тесты не дописывают строки в боевой logs/format_stats.csv (п.38 журнала).

    Прогон test_pipeline/test_serve/test_variations добавлял строки source=test
    в реальный CSV (24.10 проверено: 1692 -> 1696 строк). Конфиги из load_config
    получают STATS_PATH во tmp_path; прямые Config() и так не пишут CSV —
    дефолт stats_path теперь None (kino/config.py).
    """
    monkeypatch.setenv("STATS_PATH", str(tmp_path / "format_stats.csv"))
