"""Тесты публикации в Telegram."""

from unittest.mock import MagicMock, patch

import pytest
import requests
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError

from kino.config import Config, Settings
from kino.publish import (
    _proxies,
    _retry_after,
    _send_with_retry,
    notify_admin,
    publish,
    send_photo,
    send_text,
)


def _make_config(proxy_url: str | None = None, max_attempts: int = 5, backoff: float = 2.0) -> Config:
    return Config(
        sources=[],
        weights={},
        bad=[],
        sad=[],
        settings=Settings(retry_max_attempts=max_attempts, retry_backoff=backoff),
        llm_base_url="",
        llm_api_key="",
        draft_model="",
        judge_model="",
        bot_token="bot-token",
        channel_chat_id="-100123",
        admin_chat_id="-100456",
        proxy_url=proxy_url,
    )


def _response(status: int, body: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = body if body is not None else {}
    if status >= 400:
        response.raise_for_status.side_effect = HTTPError(response=response)
    else:
        response.raise_for_status.return_value = None
    return response


def test_proxies_none_when_no_proxy():
    cfg = _make_config(proxy_url=None)
    assert _proxies(cfg) is None


def test_proxies_both_http_and_https():
    cfg = _make_config(proxy_url="http://proxy.example:8080")
    proxies = _proxies(cfg)
    assert proxies == {
        "http": "http://proxy.example:8080",
        "https": "http://proxy.example:8080",
    }


def test_send_photo_passes_proxies():
    cfg = _make_config(proxy_url="http://proxy.example:8080")
    with patch("kino.publish.requests.post") as mock_post:
        mock_post.return_value = _response(200)
        result = send_photo(cfg, "-100123", "http://img.test/x.jpg", "caption")

    assert result is True
    call = mock_post.call_args
    assert call.kwargs["proxies"] == {
        "http": "http://proxy.example:8080",
        "https": "http://proxy.example:8080",
    }


def test_send_text_no_proxy():
    cfg = _make_config(proxy_url=None)
    with patch("kino.publish.requests.post") as mock_post:
        mock_post.return_value = _response(200)
        result = send_text(cfg, "-100123", "text")

    assert result is True
    call = mock_post.call_args
    assert call.kwargs["proxies"] is None


def test_notify_admin_skips_when_no_admin_id():
    cfg = _make_config()
    cfg.admin_chat_id = ""
    with patch("kino.publish.requests.post") as mock_post:
        notify_admin(cfg, "alert")
        mock_post.assert_not_called()


def test_publish_dry_run_returns_false():
    cfg = _make_config()
    with patch("kino.publish.send_photo") as mock_photo, \
         patch("kino.publish.send_text") as mock_text:
        result = publish(cfg, "caption", "http://img.test/x.jpg", dry_run=True)

    assert result is False
    mock_photo.assert_not_called()
    mock_text.assert_not_called()


def test_publish_photo_then_text():
    cfg = _make_config()
    with patch("kino.publish.send_photo") as mock_photo, \
         patch("kino.publish.send_text") as mock_text:
        mock_photo.return_value = True
        result = publish(cfg, "caption", "http://img.test/x.jpg", dry_run=False)

    assert result is True
    mock_photo.assert_called_once()
    mock_text.assert_not_called()


def test_publish_text_fallback():
    cfg = _make_config()
    with patch("kino.publish.send_photo") as mock_photo, \
         patch("kino.publish.send_text") as mock_text:
        mock_photo.return_value = False
        mock_text.return_value = True
        result = publish(cfg, "caption", "http://img.test/x.jpg", dry_run=False)

    assert result is True
    mock_photo.assert_called_once()
    mock_text.assert_called_once()


def test_publish_notifies_admin_on_failure():
    cfg = _make_config()
    with patch("kino.publish.send_photo") as mock_photo, \
         patch("kino.publish.send_text") as mock_text, \
         patch("kino.publish.notify_admin") as mock_notify:
        mock_photo.return_value = False
        mock_text.return_value = False
        result = publish(cfg, "caption", "http://img.test/x.jpg", dry_run=False)

    assert result is False
    mock_notify.assert_called_once()


# --- retry_after ---


def test_retry_after_reads_parameters():
    response = _response(429, {"ok": False, "parameters": {"retry_after": 17}})
    assert _retry_after(response, 2.0) == 17.0


def test_retry_after_falls_back_on_non_json():
    response = MagicMock()
    response.json.side_effect = ValueError("not json")
    assert _retry_after(response, 3.0) == 3.0


def test_retry_after_falls_back_on_missing_parameters():
    response = _response(429, {"ok": False})
    assert _retry_after(response, 4.0) == 4.0


# --- _send_with_retry ---


def test_send_with_retry_success_first_try():
    cfg = _make_config()
    with patch("kino.publish.requests.post") as mock_post, \
         patch("kino.publish.time.sleep") as mock_sleep:
        mock_post.return_value = _response(200)
        assert _send_with_retry(cfg, "sendMessage", {"chat_id": "1"}) is True

    assert mock_post.call_count == 1
    mock_sleep.assert_not_called()


def test_send_with_retry_429_then_success():
    cfg = _make_config(max_attempts=3, backoff=2.0)
    with patch("kino.publish.requests.post") as mock_post, \
         patch("kino.publish.time.sleep") as mock_sleep:
        mock_post.side_effect = [
            _response(429, {"parameters": {"retry_after": 5}}),
            _response(200),
        ]
        assert _send_with_retry(cfg, "sendMessage", {"chat_id": "1"}) is True

    assert mock_post.call_count == 2
    # retry_after=5 + 2**0 = 6
    mock_sleep.assert_called_once_with(6.0)


def test_send_with_retry_429_exhausts_attempts():
    cfg = _make_config(max_attempts=2, backoff=1.0)
    with patch("kino.publish.requests.post") as mock_post, \
         patch("kino.publish.time.sleep") as mock_sleep:
        mock_post.return_value = _response(429, {"parameters": {"retry_after": 1}})
        assert _send_with_retry(cfg, "sendMessage", {"chat_id": "1"}) is False

    assert mock_post.call_count == 2
    # 1+1=2, затем 1+2=3
    assert [c.args[0] for c in mock_sleep.call_args_list] == [2.0, 3.0]


def test_send_with_retry_400_does_not_retry():
    cfg = _make_config(max_attempts=5)
    with patch("kino.publish.requests.post") as mock_post, \
         patch("kino.publish.time.sleep") as mock_sleep:
        mock_post.return_value = _response(400, {"description": "Bad Request"})
        assert _send_with_retry(cfg, "sendMessage", {"chat_id": "1"}) is False

    assert mock_post.call_count == 1
    mock_sleep.assert_not_called()


def test_send_with_retry_network_error_then_success():
    cfg = _make_config(max_attempts=3, backoff=2.0)
    with patch("kino.publish.requests.post") as mock_post, \
         patch("kino.publish.time.sleep") as mock_sleep:
        mock_post.side_effect = [
            RequestsConnectionError("boom"),
            _response(200),
        ]
        assert _send_with_retry(cfg, "sendMessage", {"chat_id": "1"}) is True

    assert mock_post.call_count == 2
    mock_sleep.assert_called_once_with(2.0)


def test_send_with_retry_network_error_exhausts():
    cfg = _make_config(max_attempts=2, backoff=1.0)
    with patch("kino.publish.requests.post") as mock_post, \
         patch("kino.publish.time.sleep") as mock_sleep:
        mock_post.side_effect = RequestsConnectionError("boom")
        assert _send_with_retry(cfg, "sendMessage", {"chat_id": "1"}) is False

    assert mock_post.call_count == 2
    assert [c.args[0] for c in mock_sleep.call_args_list] == [1.0, 2.0]


def test_send_with_retry_429_without_json_body():
    cfg = _make_config(max_attempts=2, backoff=2.0)
    bad = MagicMock()
    bad.status_code = 429
    bad.json.side_effect = ValueError("html")
    bad.raise_for_status.side_effect = HTTPError(response=bad)
    with patch("kino.publish.requests.post") as mock_post, \
         patch("kino.publish.time.sleep") as mock_sleep:
        mock_post.side_effect = [bad, _response(200)]
        assert _send_with_retry(cfg, "sendMessage", {"chat_id": "1"}) is True

    # fallback backoff 2.0 + 2**0 = 3.0
    mock_sleep.assert_called_once_with(3.0)
