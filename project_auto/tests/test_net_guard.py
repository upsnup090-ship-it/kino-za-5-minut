"""Тесты сетевой политики: kino/net_guard.py."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from kino.net_guard import (
    NetworkPolicyError,
    get,
    network_flags_for_log,
    post,
    require_class,
    require_llm_base_url,
)


def test_https_allowed():
    # не бросает
    require_class("https://api.telegram.org/botX/sendMessage", "publish")


def test_http_rejected():
    with pytest.raises(NetworkPolicyError):
        require_class("http://api.telegram.org/", "publish")


def test_ip_literal_rejected():
    with pytest.raises(NetworkPolicyError):
        require_class("https://127.0.0.1/foo", "read")


def test_unknown_host_rejected():
    with pytest.raises(NetworkPolicyError):
        require_class("https://evil.com/", "read")


def test_nonstandard_port_rejected():
    with pytest.raises(NetworkPolicyError):
        require_class("https://api.telegram.org:8443/", "publish")


def test_subdomain_matches():
    require_class("https://ru.wikipedia.org/w/rest.php/v1/search/page", "read")


def test_llm_base_url_valid():
    require_llm_base_url("https://api.openai.com/v1")


def test_llm_base_url_invalid():
    with pytest.raises(NetworkPolicyError):
        require_llm_base_url("https://api.telegram.org/v1")


def test_loopback_class():
    require_class("http://127.0.0.1:8812", "loopback")
    with pytest.raises(NetworkPolicyError):
        require_class("https://evil.com/", "loopback")


@patch("kino.net_guard.requests.get")
def test_get_uses_no_redirects(mock_get):
    get("https://api.telegram.org/", net_class="publish")
    _, kwargs = mock_get.call_args
    assert kwargs["allow_redirects"] is False


@patch("kino.net_guard.requests.post")
def test_post_rejects_unknown_host(mock_post):
    with pytest.raises(NetworkPolicyError):
        post("https://evil.com/", net_class="publish", data={})
    mock_post.assert_not_called()


def test_network_flags_for_log():
    assert network_flags_for_log() == "нет"
    assert network_flags_for_log(read="deadline.com") == "READ: deadline.com"
    assert network_flags_for_log(llm="api.openai.com") == "LLM: api.openai.com"
    assert (
        network_flags_for_log(publish="api.telegram.org -> @channel")
        == "PUBLISH: api.telegram.org -> @channel"
    )
    assert network_flags_for_log(loopback="127.0.0.1:8812") == "LOOPBACK: 127.0.0.1:8812"