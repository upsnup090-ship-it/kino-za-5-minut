"""Публикация в Telegram через Bot API."""

from __future__ import annotations

import json
import logging
import time
from urllib.parse import parse_qs, urlparse

import requests
from requests.exceptions import HTTPError, RequestException

from kino.config import Config

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"


class PublishError(Exception):
    """Ошибка публикации."""


def _api_url(token: str, method: str) -> str:
    return f"{API_BASE}/bot{token}/{method}"


def normalize_proxy_url(raw: str) -> str:
    """Приводит PROXY_URL к формату, понятному requests.

    Поддерживает:
      - socks5://user:pass@host:port  (как есть)
      - http://user:pass@host:port    (как есть)
      - https://t.me/socks?server=...&port=...&user=...&pass=...  (ссылка Telegram)
    """
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith("https://t.me/socks") or raw.startswith("http://t.me/socks"):
        parsed = urlparse(raw)
        q = parse_qs(parsed.query)
        server = (q.get("server") or [""])[0]
        port = (q.get("port") or [""])[0]
        user = (q.get("user") or [""])[0]
        password = (q.get("pass") or [""])[0]
        if not server or not port:
            raise PublishError("в ссылке t.me/socks нет server или port")
        auth = f"{user}:{password}@" if user else ""
        return f"socks5://{auth}{server}:{port}"
    return raw


def _proxies(cfg: Config) -> dict[str, str] | None:
    """Прокси только для Telegram. Пусто → None, работаем напрямую."""
    if not cfg.proxy_url:
        return None
    url = normalize_proxy_url(cfg.proxy_url)
    return {"http": url, "https": url}


def _retry_after(response: requests.Response, fallback: float) -> float:
    """Достаёт retry_after из тела ответа 429. Тело может быть не-JSON."""
    try:
        data = response.json()
    except ValueError:
        return fallback
    params = data.get("parameters") if isinstance(data, dict) else None
    if not isinstance(params, dict):
        return fallback
    try:
        return float(params.get("retry_after"))
    except (TypeError, ValueError):
        return fallback


def _send_with_retry(cfg: Config, method: str, payload: dict) -> bool:
    """Отправка с повторами.

    429 — ждём retry_after от Telegram плюс экспоненциальный хвост.
    Сетевая ошибка — ждём base_delay * (attempt + 1).
    Прочие HTTP-ошибки (400, 401, 403) не повторяем: они не пройдут сами.
    """
    max_attempts = max(1, cfg.settings.retry_max_attempts)
    base_delay = cfg.settings.retry_backoff

    for attempt in range(max_attempts):
        try:
            response = requests.post(
                _api_url(cfg.bot_token, method),
                data=payload,
                timeout=cfg.settings.telegram_timeout,
                proxies=_proxies(cfg),
            )
            response.raise_for_status()
            return True
        except HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status != 429:
                logger.warning("%s: HTTP %s, повтор не поможет", method, status)
                return False
            wait = _retry_after(exc.response, base_delay) + (2**attempt)
            logger.warning(
                "%s: 429, ждём %.0fс (попытка %d/%d)",
                method,
                wait,
                attempt + 1,
                max_attempts,
            )
            time.sleep(wait)
        except RequestException as exc:
            wait = base_delay * (attempt + 1)
            logger.warning(
                "%s: %s, ждём %.0fс (попытка %d/%d)",
                method,
                exc.__class__.__name__,
                wait,
                attempt + 1,
                max_attempts,
            )
            time.sleep(wait)

    logger.error("%s: все %d попыток исчерпаны", method, max_attempts)
    return False


def send_photo(cfg: Config, chat_id: str, image_url: str, caption: str) -> bool:
    payload = {
        "chat_id": chat_id,
        "photo": image_url,
        "caption": caption,
        "parse_mode": cfg.settings.parse_mode,
    }
    return _send_with_retry(cfg, "sendPhoto", payload)


def send_text(cfg: Config, chat_id: str, text: str) -> bool:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": cfg.settings.parse_mode,
    }
    return _send_with_retry(cfg, "sendMessage", payload)


def send_link(
    cfg: Config,
    chat_id: str,
    text: str,
    url: str,
    *,
    large_media: bool = True,
    above_text: bool = False,
) -> bool:
    """Пост-ссылка с превью (в т.ч. YouTube — превью играет видео)."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": cfg.settings.parse_mode,
        "link_preview_options": json.dumps(
            {
                "url": url,
                "prefer_large_media": large_media,
                "prefer_small_media": not large_media,
                "show_above_text": above_text,
            }
        ),
    }
    return _send_with_retry(cfg, "sendMessage", payload)


def send_media_group(
    cfg: Config,
    chat_id: str,
    image_urls: list[str],
    caption: str,
) -> bool:
    """Карусель из 2–4 фото, подпись на первом. Больше 4 — Telegram не даст."""
    urls = [u for u in image_urls if u][:4]
    if len(urls) < 2:
        return False
    media = [{"type": "photo", "media": url} for url in urls]
    media[0]["caption"] = caption
    media[0]["parse_mode"] = cfg.settings.parse_mode
    payload = {
        "chat_id": chat_id,
        "media": json.dumps(media, ensure_ascii=False),
    }
    return _send_with_retry(cfg, "sendMediaGroup", payload)


def send_poll(
    cfg: Config,
    chat_id: str,
    question: str,
    options: list[str],
) -> bool:
    """Опрос в канале. Боты умеют sendPoll в каналах; анонимное голосование."""
    opts = [o for o in (x.strip() for x in options) if o][:10]
    if len(opts) < 2:
        return False
    payload = {
        "chat_id": chat_id,
        "question": question[:300],
        "options": json.dumps(opts, ensure_ascii=False),
        "is_anonymous": True,
    }
    return _send_with_retry(cfg, "sendPoll", payload)


def send_poll_with_caption(
    cfg: Config,
    chat_id: str,
    caption: str,
    question: str,
    options: list[str],
) -> bool:
    """Опрос вместе с текстом поста.

    Telegram не умеет класть текст в сам опрос, поэтому текст уходит отдельным
    сообщением перед ним. Без этого вариация poll теряла всю подпись: в канал
    приходил голый вопрос без заголовка и лида.
    """
    if caption.strip() and not send_text(cfg, chat_id, caption):
        # Текст не ушёл — опрос без него выглядит обрубком, лучше не публиковать.
        logger.warning("poll: текст поста не отправлен, опрос пропущен")
        return False
    return send_poll(cfg, chat_id, question, options)


def notify_admin(cfg: Config, text: str) -> None:
    """Короткое сообщение админу. Без токенов и ключей.

    Прокси отвечает через раз (та же беда, что у graph.org), поэтому сетевые
    сбои повторяются: раньше одна неудачная попытка означала потерянное
    уведомление о сбое пайплайна. HTTP-статус тоже проверяется — иначе 403
    (бот не в чате) выглядел бы как успешная доставка.
    """
    if not cfg.admin_chat_id:
        return
    attempts = max(1, min(3, cfg.settings.retry_max_attempts))
    for attempt in range(attempts):
        try:
            response = requests.post(
                _api_url(cfg.bot_token, "sendMessage"),
                data={"chat_id": cfg.admin_chat_id, "text": text},
                timeout=cfg.settings.telegram_timeout,
                proxies=_proxies(cfg),
            )
        except RequestException as exc:
            logger.warning(
                "не удалось уведомить админа: %s (попытка %d/%d)",
                exc.__class__.__name__,
                attempt + 1,
                attempts,
            )
            if attempt < attempts - 1:
                time.sleep(cfg.settings.retry_backoff * (attempt + 1))
            continue
        if response.status_code >= 400:
            # 403 = бот не в чате с админом, 400 = неверный chat_id.
            logger.warning(
                "уведомление админу отклонено: HTTP %s %s",
                response.status_code,
                response.text[:200],
            )
            return
        return
    logger.error("уведомление админу не доставлено за %d попыток", attempts)


def publish(
    cfg: Config,
    caption: str,
    image_url: str,
    dry_run: bool,
    kind: str = "photo",
    link_url: str = "",
    extra_images: list[str] | None = None,
    poll_question: str = "",
    poll_options: list[str] | None = None,
) -> bool:
    """Публикация выбранной вариацией: photo | link | gallery | text | poll.

    Порядок фолбэков: выбранная вариация → фото → текст → ЛС админу.
    """
    if dry_run:
        logger.info("dry-run: публикация пропущена")
        return False
    if not cfg.channel_chat_id:
        raise PublishError("CHANNEL_CHAT_ID не задан")
    chat = cfg.channel_chat_id

    sent: bool | None = None
    if kind == "link" and link_url:
        sent = send_link(cfg, chat, caption, link_url)
    elif kind == "gallery":
        urls = [image_url] + list(extra_images or [])
        sent = send_media_group(cfg, chat, urls, caption)
    elif kind == "poll":
        sent = send_poll_with_caption(cfg, chat, caption, poll_question, poll_options)
    elif kind == "link" and not link_url:
        logger.warning("вариация link без link_url — фолбэк на фото")

    if sent:
        return True
    if image_url and send_photo(cfg, chat, image_url, caption):
        return True
    if send_text(cfg, chat, caption):
        return True
    notify_admin(cfg, "публикация не удалась: ни одна вариация не отбилась")
    return False
