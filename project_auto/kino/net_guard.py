"""Сетевой контроль: allowlist, канонизация адреса, запрет редиректов.

Все requests-вызовы проекта должны идти через get/post этого модуля.

Классы доступа:
    read     — RSS, Wikipedia, graph.org, quickchart.io, i.ytimg.com и др.
    llm      — LLM-провайдеры
    publish  — api.telegram.org (единственный необратимый канал)
    proxy    — прокси-хосты из переменных окружения
    loopback — 127.0.0.1 / localhost / ::1

Правила адреса: только https, только FQDN из allowlist, IP-литералы и
нестандартные порты запрещены, редиректы запрещены.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

ALLOWLIST_PATH = Path(__file__).resolve().parents[1] / "config" / "network_allowlist.yaml"


class NetworkClass:
    READ = "read"
    LLM = "llm"
    PUBLISH = "publish"
    PROXY = "proxy"
    LOOPBACK = "loopback"


class NetworkPolicyError(Exception):
    """Запрос нарушает сетевую политику."""


_ALLOWLIST: dict[str, list[str]] | None = None


def _embedded_allowlist() -> dict[str, list[str]]:
    return {
        "read": [
            "deadline.com", "variety.com", "hollywoodreporter.com",
            "slashfilm.com", "indiewire.com", "theguardian.com",
            "rollingstone.com", "avclub.com", "reddit.com", "youtube.com",
            "scmp.com", "japantimes.co.jp", "channelnewsasia.com",
            "yna.co.kr", "intermedia.ru", "film.ru",
            "i.ytimg.com", "ru.wikipedia.org", "en.wikipedia.org",
            "api.graph.org", "quickchart.io",
        ],
        "llm": [
            "api.apimart.ai",
            "generativelanguage.googleapis.com",
            "openrouter.ai",
            "api.openai.com",
            "api.groq.com",
            "ai.reqline.ru",  # свой шлюз к OpenRouter (боевой сервер)
        ],
        "publish": ["api.telegram.org"],
        "proxy": [],
        "loopback": ["127.0.0.1", "localhost", "::1"],
    }


def _load_allowlist() -> dict[str, list[str]]:
    global _ALLOWLIST
    if _ALLOWLIST is None:
        if ALLOWLIST_PATH.exists():
            import yaml

            data = yaml.safe_load(ALLOWLIST_PATH.read_text(encoding="utf-8")) or {}
            _ALLOWLIST = data
        else:
            _ALLOWLIST = _embedded_allowlist()
    return _ALLOWLIST


def reload_allowlist() -> None:
    global _ALLOWLIST
    _ALLOWLIST = None


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _normalize_host(host: str) -> str:
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def _host_matches(host: str, entry: str) -> bool:
    host = _normalize_host(host)
    entry = _normalize_host(entry)
    return host == entry or host.endswith("." + entry)


def _proxy_hosts_from_env() -> set[str]:
    hosts: set[str] = set()
    for key in ("PROXY_URL", "LLM_PROXY_URL", "GRAPH_PROXY_URL", "TELEGRAPH_PROXY_URL"):
        value = os.environ.get(key, "").strip()
        if not value:
            continue
        try:
            parsed = urlparse(value)
            if parsed.hostname:
                hosts.add(parsed.hostname.lower())
        except Exception:
            pass
    return hosts


def _canonical(url: str) -> tuple[str, str, int]:
    if not url:
        raise NetworkPolicyError("пустой URL")
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    port = parsed.port
    if scheme != "https":
        raise NetworkPolicyError(f"только https разрешён: {url}")
    if not host:
        raise NetworkPolicyError(f"URL без хоста: {url}")
    if _is_ip(host):
        raise NetworkPolicyError(f"IP-литералы запрещены: {url}")
    if port is not None and port != 443:
        raise NetworkPolicyError(f"нестандартный порт {port}: {url}")
    return scheme, host, port or 443


def require_class(url: str, expected_class: str) -> None:
    """Проверяет URL. Бросает NetworkPolicyError при нарушении политики."""
    if expected_class == NetworkClass.LOOPBACK:
        # Loopback — не внешняя сеть (AGENT.md §3): http/https и любой порт
        # допустимы, IP-литералы разрешены — ухода за 127.0.0.1 нет.
        # Внешние классы (read/llm/publish/proxy) остаются https-only ниже.
        if not url:
            raise NetworkPolicyError("пустой URL")
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ("http", "https"):
            raise NetworkPolicyError(f"недопустимая схема: {url}")
        if (parsed.hostname or "").lower() in ("127.0.0.1", "localhost", "::1"):
            return
        raise NetworkPolicyError(f"{url} — не loopback")
    _, host, _ = _canonical(url)
    cfg = _load_allowlist()
    entries = list(cfg.get(expected_class, []))
    if expected_class == NetworkClass.PROXY:
        entries = list(set(entries) | _proxy_hosts_from_env())
    for entry in entries:
        if _host_matches(host, entry):
            return
    raise NetworkPolicyError(
        f"хост {host} не входит в класс {expected_class}: {url}"
    )


def get(url: str, *, net_class: str, **kwargs: Any) -> requests.Response:
    kwargs["allow_redirects"] = kwargs.get("allow_redirects", False)
    require_class(url, net_class)
    return requests.get(url, **kwargs)


def post(url: str, *, net_class: str, **kwargs: Any) -> requests.Response:
    kwargs["allow_redirects"] = kwargs.get("allow_redirects", False)
    require_class(url, net_class)
    return requests.post(url, **kwargs)


def require_llm_base_url(url: str) -> None:
    """Проверка LLM_BASE_URL при загрузке конфига."""
    require_class(url, NetworkClass.LLM)


def network_flags_for_log(**kwargs: Any) -> str:
    """Формирует строку для поля СЕТЬ в журнале."""
    if kwargs.get("loopback"):
        return f"LOOPBACK: {kwargs['loopback']}"
    if kwargs.get("publish"):
        return f"PUBLISH: {kwargs['publish']}"
    if kwargs.get("llm"):
        return f"LLM: {kwargs['llm']}"
    if kwargs.get("read"):
        return f"READ: {kwargs['read']}"
    return "нет"


def audit_code() -> list[str]:
    """Ищет прямые вызовы requests.get/post в коде."""
    pattern = re.compile(r"requests\.(?:get|post|put|delete|patch)\s*\(")
    violations: list[str] = []
    root = Path(__file__).resolve().parents[1]
    for path in root.rglob("*.py"):
        if path.name == "net_guard.py":
            continue
        if "venv" in path.parts or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'"):
                continue
            if pattern.search(line):
                violations.append(f"{path}:{i}: прямой requests-вызов")
                break
    return violations


def main() -> int:
    import sys

    if "--audit" in sys.argv:
        violations = audit_code()
        if violations:
            print("Нарушения сетевой политики:")
            for v in violations:
                print(" ", v)
            return 1
        print("OK: прямых requests-вызовов не найдено")
        return 0
    print("Использование: python -m kino.net_guard --audit")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())