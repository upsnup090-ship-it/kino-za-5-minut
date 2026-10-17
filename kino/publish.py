"""Публикация в Telegram через Bot API."""

from __future__ import annotations

import logging

import requests

from kino.config import Config

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"


class PublishError(Exception):
    """Ошибка публикации."""


def _api_url(token: str, method: str) -> str:
    return f"{API_BASE}/bot{token}/{method}"


def send_photo(cfg: Config, chat_id: str, image_url: str, caption: str) -> bool:
    try:
        response = requests.post(
            _api_url(cfg.bot_token, "sendPhoto"),
            data={
                "chat_id": chat_id,
                "photo": image_url,
                "caption": caption,
                "parse_mode": cfg.settings.parse_mode,
            },
            timeout=cfg.settings.llm_timeout,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("sendPhoto не удался: %s", exc.__class__.__name__)
        return False


def send_text(cfg: Config, chat_id: str, text: str) -> bool:
    try:
        response = requests.post(
            _api_url(cfg.bot_token, "sendMessage"),
            data={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": cfg.settings.parse_mode,
            },
            timeout=cfg.settings.llm_timeout,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("sendMessage не удался: %s", exc.__class__.__name__)
        return False


def notify_admin(cfg: Config, text: str) -> None:
    """Короткое сообщение админу. Без токенов и ключей."""
    if not cfg.admin_chat_id:
        return
    try:
        requests.post(
            _api_url(cfg.bot_token, "sendMessage"),
            data={"chat_id": cfg.admin_chat_id, "text": text},
            timeout=cfg.settings.llm_timeout,
        )
    except requests.RequestException:
        logger.warning("не удалось уведомить админа")


def publish(cfg: Config, caption: str, image_url: str, dry_run: bool) -> bool:
    """Фото → текст → ЛС админу при ошибке."""
    if dry_run:
        logger.info("dry-run: публикация пропущена")
        return False
    if not cfg.channel_chat_id:
        raise PublishError("CHANNEL_CHAT_ID не задан")
    if image_url and send_photo(cfg, cfg.channel_chat_id, image_url, caption):
        return True
    if send_text(cfg, cfg.channel_chat_id, caption):
        return True
    notify_admin(cfg, "публикация не удалась: и фото, и текст отбились")
    return False