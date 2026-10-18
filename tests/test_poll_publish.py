"""Регрессии вариации poll: подпись не должна теряться.

Пункт 2 из docs/todo.md: в канал уходил голый вопрос без текста поста.
"""

from __future__ import annotations

from unittest.mock import patch

from kino.config import Config, Settings
from kino.publish import publish, send_poll_with_caption


def _cfg() -> Config:
    return Config(
        settings=Settings(),
        bot_token="bot-token",
        channel_chat_id="-100123",
        admin_chat_id="-100456",
    )


def test_poll_sends_caption_before_question():
    cfg = _cfg()
    calls: list[tuple[str, dict]] = []

    def fake_send(cfg, method, payload):
        calls.append((method, payload))
        return True

    with patch("kino.publish._send_with_retry", side_effect=fake_send):
        result = send_poll_with_caption(cfg, "-100123", "ТЕКСТ ПОСТА", "Вопрос?", ["a", "b"])

    assert result is True
    methods = [method for method, _ in calls]
    assert methods == ["sendMessage", "sendPoll"]
    assert calls[0][1]["text"] == "ТЕКСТ ПОСТА"
    assert calls[1][1]["question"] == "Вопрос?"


def test_poll_without_caption_sends_only_poll():
    cfg = _cfg()
    calls: list[str] = []

    def fake_send(cfg, method, payload):
        calls.append(method)
        return True

    with patch("kino.publish._send_with_retry", side_effect=fake_send):
        result = send_poll_with_caption(cfg, "-100123", "   ", "Вопрос?", ["a", "b"])

    assert result is True
    assert calls == ["sendPoll"]


def test_poll_skipped_when_caption_fails_to_send():
    """Текст не ушёл — опрос без него выглядит обрубком, лучше не публиковать."""
    cfg = _cfg()
    calls: list[str] = []

    def fake_send(cfg, method, payload):
        calls.append(method)
        return False

    with patch("kino.publish._send_with_retry", side_effect=fake_send):
        result = send_poll_with_caption(cfg, "-100123", "ТЕКСТ", "Вопрос?", ["a", "b"])

    assert result is False
    assert calls == ["sendMessage"]


def test_publish_poll_uses_caption():
    """publish() с kind=poll обязан донести подпись, а не только вопрос."""
    cfg = _cfg()
    calls: list[tuple[str, dict]] = []

    def fake_send(cfg, method, payload):
        calls.append((method, payload))
        return True

    with patch("kino.publish._send_with_retry", side_effect=fake_send):
        result = publish(
            cfg,
            "ПОДПИСЬ ПОСТА",
            "",
            dry_run=False,
            kind="poll",
            poll_question="Вопрос?",
            poll_options=["a", "b"],
        )

    assert result is True
    sent_texts = [p.get("text") for m, p in calls if m == "sendMessage"]
    assert "ПОДПИСЬ ПОСТА" in sent_texts


def test_publish_poll_without_question_falls_back_to_text():
    """Опрос без вопроса не отправляем — уходим в текстовый фолбэк."""
    cfg = _cfg()
    calls: list[str] = []

    def fake_send(cfg, method, payload):
        calls.append(method)
        return True

    with patch("kino.publish._send_with_retry", side_effect=fake_send):
        result = publish(
            cfg,
            "ПОДПИСЬ",
            "",
            dry_run=False,
            kind="poll",
            poll_question="",
            poll_options=[],
        )

    assert result is True
    assert "sendPoll" not in calls