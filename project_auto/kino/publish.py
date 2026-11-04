"""Публикация в Telegram через Bot API.

Все функции отправки (*send_photo*, *send_text*, …) и сама `publish` теперь
принимают опциональные параметры ``bot_token`` и ``channel_chat_id``.
Если они не переданы — используются глобальные `cfg.bot_token` / `cfg.channel_chat_id`
(обратная совместимость).

`publish()` принимает эти параметры напрямую.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from urllib.parse import parse_qs, urlparse

import requests
from requests.exceptions import HTTPError, RequestException

from kino.config import Config
from kino.net_guard import post as http_post

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"


class PublishError(Exception):
    """Ошибка публикации.

    Текст уходит в лог и в уведомление админу, поэтому токен вычищается на
    входе — по той же причине, что в `TgError`.
    """

    def __init__(self, message: str) -> None:
        from kino.logging_util import sanitize

        super().__init__(sanitize(message))


def _api_url(token: str, method: str) -> str:
    """URL метода Bot API. Без токена — понятная ошибка, а не /bot/method → 404.

    Токен обязателен только на пути публикации: `kino schedule`, `kino draft`
    и dry-run работают без него (иначе правка расписания требовала бы боевых
    секретов на машине, где публикации нет).
    """
    if not token:
        raise PublishError("BOT_TOKEN не задан: публикация в Telegram невозможна")
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
        return float(params.get("retry_after"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def _resolve_token(cfg: Config, bot_token: str | None) -> str:
    """Вернуть bot_token или cfg.bot_token (обратная совместимость)."""
    return bot_token if bot_token is not None else cfg.bot_token


def _send_with_retry(
    cfg: Config,
    method: str,
    payload: dict,
    *,
    bot_token: str | None = None,
) -> bool:
    """Отправка с повторами.

    ``bot_token`` — токен бота-клиента. Если не передан, используется
    ``cfg.bot_token``.

    429 — ждём retry_after от Telegram плюс экспоненциальный хвост.
    Сетевая ошибка — ждём base_delay * (attempt + 1).
    Прочие HTTP-ошибки (400, 401, 403) не повторяем: они не пройдут сами.
    """
    max_attempts = max(1, cfg.settings.retry_max_attempts)
    base_delay = cfg.settings.retry_backoff
    token = _resolve_token(cfg, bot_token)

    for attempt in range(max_attempts):
        try:
            response = http_post(
                _api_url(token, method),
                data=payload,
                timeout=cfg.settings.telegram_timeout,
                proxies=_proxies(cfg),
                net_class="publish",
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


def send_photo(
    cfg: Config,
    chat_id: str,
    image_url: str,
    caption: str,
    *,
    bot_token: str | None = None,
) -> bool:
    payload = {
        "chat_id": chat_id,
        "photo": image_url,
        "caption": caption,
        "parse_mode": cfg.settings.parse_mode,
    }
    return _send_with_retry(cfg, "sendPhoto", payload, bot_token=bot_token)


def send_text(
    cfg: Config,
    chat_id: str,
    text: str,
    *,
    bot_token: str | None = None,
) -> bool:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": cfg.settings.parse_mode,
    }
    return _send_with_retry(cfg, "sendMessage", payload, bot_token=bot_token)


def send_link(
    cfg: Config,
    chat_id: str,
    text: str,
    url: str,
    *,
    large_media: bool = True,
    above_text: bool = False,
    bot_token: str | None = None,
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
    return _send_with_retry(cfg, "sendMessage", payload, bot_token=bot_token)


def send_media_group(
    cfg: Config,
    chat_id: str,
    image_urls: list[str],
    caption: str,
    *,
    bot_token: str | None = None,
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
    return _send_with_retry(cfg, "sendMediaGroup", payload, bot_token=bot_token)


def send_poll(
    cfg: Config,
    chat_id: str,
    question: str,
    options: list[str],
    *,
    bot_token: str | None = None,
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
    return _send_with_retry(cfg, "sendPoll", payload, bot_token=bot_token)


def send_poll_with_caption(
    cfg: Config,
    chat_id: str,
    caption: str,
    question: str,
    options: list[str],
    *,
    bot_token: str | None = None,
) -> bool:
    """Опрос вместе с текстом поста.

    Telegram не умеет класть текст в сам опрос, поэтому текст уходит отдельным
    сообщением перед ним. Без этого вариация poll теряла всю подпись: в канал
    приходил голый вопрос без заголовка и лида.
    """
    if caption.strip() and not send_text(cfg, chat_id, caption, bot_token=bot_token):
        # Текст не ушёл — опрос без него выглядит обрубком, лучше не публиковать.
        logger.warning("poll: текст поста не отправлен, опрос пропущен")
        return False
    return send_poll(cfg, chat_id, question, options, bot_token=bot_token)


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
            response = http_post(
                _api_url(cfg.bot_token, "sendMessage"),
                data={"chat_id": cfg.admin_chat_id, "text": text},
                timeout=cfg.settings.telegram_timeout,
                proxies=_proxies(cfg),
                net_class="publish",
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


def _hash_post_content(caption: str, image_url: str, kind: str, link_url: str) -> str:
    """Хэширует содержимое поста для дедупликации."""
    key = f"{caption}{image_url}{kind}{link_url}"
    return hashlib.sha256(key.encode()).hexdigest()


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
    seen_hashes: set[str] | None = None,
    bot_token: str | None = None,
    channel_chat_id: str | None = None,
) -> bool:
    """Публикация выбранной вариацией: photo | link | gallery | text | poll.

    ``bot_token`` и ``channel_chat_id`` — опциональны. Если не переданы,
    используются глобальные ``cfg.bot_token`` / ``cfg.channel_chat_id``
    (обратная совместимость с существующими вызовами).

    Порядок фолбэков: выбранная вариация → фото → текст → ЛС админу.

    seen_hashes — хэши уже опубликованных постов (state.recent_post_hashes).
    Повтор того же поста не отправляется: после падения между send и
    save_state ссылка могла не попасть в used_links, а пост уже вышел.
    """
    if dry_run:
        logger.info("dry-run: публикация пропущена")
        return False
    logger.info("публикация: kind=%s", kind)

    chat_id = channel_chat_id if channel_chat_id is not None else cfg.channel_chat_id
    if not chat_id:
        raise PublishError("CHANNEL_CHAT_ID не задан")
    chat = chat_id
    token = bot_token  # может быть None — _send_with_retry подставит cfg.bot_token

    post_hash = _hash_post_content(caption, image_url, kind, link_url)
    if seen_hashes is not None and post_hash in seen_hashes:
        logger.warning("пост с таким хэшем уже публиковался (%s), пропускаем", post_hash[:12])
        return False

    sent: bool | None = None
    if kind == "link" and link_url:
        sent = send_link(cfg, chat, caption, link_url, bot_token=token)
    elif kind == "gallery":
        urls = [image_url] + list(extra_images or [])
        sent = send_media_group(cfg, chat, urls, caption, bot_token=token)
    elif kind == "poll":
        # poll_options объявлен как `list[str] | None` (дефолт None), а
        # send_poll перебирает options — None давал TypeError вместо фолбэка
        # на фото/текст. Пустой список безопасен: send_poll вернёт False.
        sent = send_poll_with_caption(
            cfg, chat, caption, poll_question, poll_options or [],
            bot_token=token,
        )
    elif kind == "link" and not link_url:
        logger.warning("вариация link без link_url — фолбэк на фото")

    if sent:
        return True
    if image_url and send_photo(cfg, chat, image_url, caption, bot_token=token):
        return True
    if send_text(cfg, chat, caption, bot_token=token):
        return True
    notify_admin(cfg, "публикация не удалась: ни одна вариация не отбилась")
    return False