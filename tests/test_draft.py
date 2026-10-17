"""Тесты draft.py с моком HTTP — без реальных вызовов."""

import json

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
    with pytest.raises(DraftError, match="соединения"):
        draft(_make_choice(), cfg)


def test_draft_empty_model_raises():
    cfg = _make_config()
    cfg.draft_model = ""
    with pytest.raises(DraftError, match="DRAFT_MODEL"):
        draft(_make_choice(), cfg)