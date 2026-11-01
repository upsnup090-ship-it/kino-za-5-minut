"""Тесты draft.py с моком HTTP — без реальных вызовов."""

import json
import time

import pytest
import requests

from kino.collect import FeedItem
from kino.choose import Choice
from kino.config import Config, Settings
from kino.draft import DraftError, build_prompt, draft, parse_draft


def _make_config() -> Config:
    return Config(
        settings=Settings(),
        llm_base_url="https://openrouter.ai/api/v1",
        llm_api_key="sk-or-test",
        draft_model="meta-llama/llama-3.1-8b-instruct:free",
    )


def _make_choice() -> Choice:
    item = FeedItem(
        source="Deadline",
        title="Some movie news",
        link="http://example.com/1",
        pub_date="",
        ts=0.0,
        snippet="A snippet about a film.",
    )
    return Choice(item=item, format="news", quality_score=5, sad_topic=False)


def _ok_response(content: str) -> requests.Response:
    resp = requests.Response()
    resp.status_code = 200
    resp._content = json.dumps(
        {"choices": [{"message": {"content": content}}]}
    ).encode("utf-8")
    return resp


def test_build_prompt_uses_draft_model():
    cfg = _make_config()
    body = build_prompt(_make_choice(), cfg)
    assert body["model"] == "meta-llama/llama-3.1-8b-instruct:free"
    assert body["messages"][0]["role"] == "system"
    assert "Заголовок" in body["messages"][1]["content"]


def _choice_with_ts(ts: float) -> Choice:
    item = FeedItem(
        source="Variety",
        title="It: Welcome to Derry Surprise Halloween Episode Drops on HBO Max",
        link="https://variety.com/1",
        pub_date="Sat, 31 Oct 2026 16:50:11 +0000",
        ts=ts,
        snippet="A surprise episode.",
    )
    return Choice(item=item, format="facts", quality_score=5, sad_topic=False)


def test_draft_prompt_has_pub_date():
    """Дата материала доходит до модели: без неё она выдумывает год.

    Живой дефект 31.10.2026: подпись «Материал Variety, 2024» на новости,
    вышедшей в тот же день. Модель физически не видела дату в промпте.
    """
    cfg = _make_config()
    # 31.10.2026 16:50:11 UTC = 19:50:11 MSK того же дня.
    ts = 1793465411.0
    body = build_prompt(_choice_with_ts(ts), cfg)
    user = body["messages"][1]["content"]
    assert "Дата материала: 31 октября 2026" in user
    # Плейсхолдер не остался неподставленным.
    assert "{pub_date}" not in user


def test_draft_prompt_no_date_when_unknown():
    """ts=0 (часть Reddit-лент) — «(неизвестна)», а не выдуманная дата."""
    cfg = _make_config()
    body = build_prompt(_choice_with_ts(0.0), cfg)
    user = body["messages"][1]["content"]
    assert "Дата материала: (неизвестна)" in user
    assert "1970" not in user
    assert "{pub_date}" not in user


def test_draft_prompt_date_uses_local_timezone():
    """Дата в промпте — по Москве, а не по UTC.

    Новость 31.10.2026 23:30 UTC — это уже 1 ноября по Москве, и модель
    должна получить 1 ноября, иначе в подписи окажется вчерашний день.
    """
    cfg = _make_config()
    ts = 1793499000.0  # 2026-10-31 23:30:00 UTC
    body = build_prompt(_choice_with_ts(ts), cfg)
    user = body["messages"][1]["content"]
    assert "Дата материала: 1 ноября 2026" in user


def test_parse_draft_extracts_json():
    content = 'вот ответ: {"headline": "Шапка", "text": "Текст"}'
    data = parse_draft(content)
    assert data["headline"] == "Шапка"
    assert data["text"] == "Текст"


def test_parse_draft_no_json_raises():
    with pytest.raises(DraftError):
        parse_draft("нет JSON тут")


def test_draft_returns_parsed_dict(monkeypatch):
    cfg = _make_config()

    def fake_post(url, json=None, headers=None, timeout=None, proxies=None):
        return _ok_response('{"headline": "Шапка", "text": "Текст", "question": "Вопрос?"}')

    monkeypatch.setattr(requests, "post", fake_post)
    data = draft(_make_choice(), cfg)
    assert data["headline"] == "Шапка"
    assert data["question"] == "Вопрос?"


def test_draft_model_not_found_raises(monkeypatch):
    cfg = _make_config()

    def fake_post(url, json=None, headers=None, timeout=None, proxies=None):
        resp = requests.Response()
        resp.status_code = 400
        resp._content = b'{"error": "model_not_found"}'
        return resp

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(DraftError, match="не найдена"):
        draft(_make_choice(), cfg)


def test_draft_401_raises(monkeypatch):
    cfg = _make_config()

    def fake_post(url, json=None, headers=None, timeout=None, proxies=None):
        resp = requests.Response()
        resp.status_code = 401
        resp._content = b'{"error": "unauthorized"}'
        return resp

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(DraftError, match="401"):
        draft(_make_choice(), cfg)


def test_draft_network_error_raises(monkeypatch):
    cfg = _make_config()

    def fake_post(url, json=None, headers=None, timeout=None, proxies=None):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(requests, "post", fake_post)
    # Лестница бюджетов × ретраи = 9 пауз по 2/4/8с = 42с реального сна.
    # Тест проверяет обработку ошибки, а не терпение: паузы подменяем.
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    with pytest.raises(DraftError, match="соединения"):
        draft(_make_choice(), cfg)


def test_draft_empty_model_raises():
    cfg = _make_config()
    cfg.draft_model = ""
    with pytest.raises(DraftError, match="DRAFT_MODEL"):
        draft(_make_choice(), cfg)