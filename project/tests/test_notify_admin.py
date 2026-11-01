"""Регрессии уведомления админа.

Найдено живым прогоном: `notify_admin` глотал причину сбоя — одна неудачная
попытка через прокси означала потерянное уведомление, а HTTP-ошибка (403, бот
не в чате) выглядела как успешная доставка.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from kino.config import Config, Settings
from kino.publish import notify_admin


def _cfg(**kwargs) -> Config:
    return Config(
        settings=Settings(**kwargs),
        bot_token="bot-token",
        admin_chat_id="-100456",
    )


def _response(status: int = 200, text: str = "{}") -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.text = text
    return response


def test_notify_admin_skips_without_chat_id():
    cfg = _cfg()
    cfg.admin_chat_id = ""
    with patch("kino.publish.requests.post") as mock_post:
        notify_admin(cfg, "alert")
    mock_post.assert_not_called()


def test_notify_admin_sends_when_ok():
    cfg = _cfg()
    with patch("kino.publish.requests.post", return_value=_response(200)) as mock_post:
        notify_admin(cfg, "alert")
    assert mock_post.call_count == 1
    assert mock_post.call_args.kwargs["data"]["text"] == "alert"


def test_notify_admin_retries_on_network_error():
    """Одна неудачная попытка через прокси не должна терять уведомление."""
    cfg = _cfg(retry_max_attempts=3, retry_backoff=0)
    with patch("kino.publish.requests.post") as mock_post, \
         patch("kino.publish.time.sleep"):
        mock_post.side_effect = [
            requests.ConnectionError("proxy timeout"),
            _response(200),
        ]
        notify_admin(cfg, "alert")
    assert mock_post.call_count == 2


def test_notify_admin_gives_up_after_attempts():
    cfg = _cfg(retry_max_attempts=3, retry_backoff=0)
    with patch("kino.publish.requests.post") as mock_post, \
         patch("kino.publish.time.sleep"):
        mock_post.side_effect = requests.ConnectionError("dead proxy")
        notify_admin(cfg, "alert")
    assert mock_post.call_count == 3


def test_notify_admin_does_not_retry_on_http_error():
    """403 (бот не в чате) повтором не лечится — только лог."""
    cfg = _cfg(retry_max_attempts=3, retry_backoff=0)
    with patch("kino.publish.requests.post", return_value=_response(403, "forbidden")) as mock_post:
        notify_admin(cfg, "alert")
    assert mock_post.call_count == 1


def test_notify_admin_never_raises():
    """Уведомление не должно ронять пайплайн ни при каких обстоятельствах."""
    cfg = _cfg(retry_max_attempts=1, retry_backoff=0)
    with patch("kino.publish.requests.post", side_effect=requests.Timeout("boom")):
        notify_admin(cfg, "alert")