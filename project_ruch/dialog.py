"""Диалог ручного постинга (FSM).

Состояние живёт в ``ruch.db`` (таблица ``post_drafts``), поэтому переживает
рестарт диспетчера. Один диалог на пользователя.

Поток:

    /post
      → await_channel   выбор канала кнопками (client_channels purpose=manual|both)
      → await_content   текст или медиа (фото/документ/видео)
      → await_confirm   «Отправить» / «Изменить» / «В резерв» / «Отмена»
      → отправка через manual_post.publish_post

Кнопки: callback_data вида ``cb:<команда>``. Диспетчер превращает его в
обычный текст и отдаёт сюда — поэтому кнопка и команда идут одним путём.
Текстовые команды работают наравне с кнопками.

Команды вне диалога: ``/posts`` — последние посты, ``/del_last`` — удалить
последний отправленный, ``/reserve`` — отложить текущий черновик.
"""

from __future__ import annotations

import logging
from typing import Any

from pro_code.clients_db import ClientsDb
from pro_code.tg_api import TgError, inline_keyboard, send_message
from project_ruch import manual_post
from project_ruch.ruch_db import RuchDb

logger = logging.getLogger(__name__)

#: Состояния диалога.
STATE_IDLE = "idle"
STATE_CHANNEL = "await_channel"
STATE_CONTENT = "await_content"
STATE_CONFIRM = "await_confirm"

#: Сколько каналов показывать в выборе.
MAX_CHANNEL_CHOICES = 10

#: Сколько постов показывать в /posts.
POSTS_PAGE = 10

#: Префикс callback_data кнопок диалога.
CB_PREFIX = "cb:"


def _send(token: str, chat_id: int, text: str, **kwargs: Any) -> None:
    try:
        send_message(token, chat_id, text, **kwargs)
    except TgError as exc:
        logger.warning("не удалось ответить в чат %s: %s", chat_id, exc)


def _manual_channels(clients_db: ClientsDb, user_id: int) -> list[dict[str, Any]]:
    """Каналы клиента, куда разрешён ручной постинг."""
    return clients_db.list_channels(user_id, purpose="manual")


def _channel_label(ch: dict[str, Any]) -> str:
    title = ch.get("title") or f"чат {ch['chat_id']}"
    icon = {"channel": "📢", "supergroup": "👥", "group": "👥", "chat": "👥"}.get(
        ch.get("chat_type", ""), "💬"
    )
    return f"{icon} {title} (`{ch['chat_id']}`)"


def _channel_button_label(ch: dict[str, Any], index: int) -> str:
    """Короткая подпись кнопки: Telegram режет длинные."""
    title = ch.get("title") or f"чат {ch['chat_id']}"
    icon = {"channel": "📢", "supergroup": "👥", "group": "👥", "chat": "👥"}.get(
        ch.get("chat_type", ""), "💬"
    )
    label = f"{index}. {icon} {title}"
    return label if len(label) <= 60 else label[:57] + "…"


def _channel_keyboard(channels: list[dict[str, Any]]) -> dict:
    """Клавиатура выбора канала: по одной кнопке в ряд."""
    rows = [
        [(_channel_button_label(ch, i), f"{CB_PREFIX}{i}")]
        for i, ch in enumerate(channels[:MAX_CHANNEL_CHOICES], 1)
    ]
    rows.append([("❌ Отмена", f"{CB_PREFIX}/cancel")])
    return inline_keyboard(rows)


def _confirm_keyboard() -> dict:
    return inline_keyboard([
        [("✅ Отправить", f"{CB_PREFIX}/send"), ("✏️ Изменить", f"{CB_PREFIX}/edit")],
        [("⏳ В резерв", f"{CB_PREFIX}/reserve"), ("❌ Отмена", f"{CB_PREFIX}/cancel")],
    ])


def _content_keyboard() -> dict:
    return inline_keyboard([[("❌ Отмена", f"{CB_PREFIX}/cancel")]])


def _posts_keyboard() -> dict:
    return inline_keyboard([
        [("✍️ Новый пост", f"{CB_PREFIX}/post"), ("🗑 Удалить последний", f"{CB_PREFIX}/del_last")],
    ])


def start(token: str, chat_id: int, user_id: int, clients_db: ClientsDb, ruch_db: RuchDb) -> None:
    """Начать диалог: /post."""
    # Не затирать активный диалог повторяющихся сообщений.
    current = (ruch_db.get_draft(user_id) or {}).get("state") or ""
    if current.startswith("repeating:"):
        _send(token, chat_id,
              "⏳ Сначала заверши создание повторяющегося сообщения\n"
              "(отправь его текст/время/дни или `/cancel`).")
        return
    channels = _manual_channels(clients_db, user_id)
    if not channels:
        _send(
            token, chat_id,
            "✍️ **Ручной постинг**\n\n"
            "У тебя нет каналов для ручного постинга.\n\n"
            "Перешли любой пост из своего канала или группы сюда — "
            "я запомню его. Или `/add_manual <chat_id>`.",
        )
        return

    ruch_db.save_draft(user_id, state=STATE_CHANNEL, channel_chat_id=None,
                       text="", media_type="text", media_path=None, post_id=None)

    lines = ["✍️ **Ручной постинг**\n", "Куда публикуем?\n"]
    for i, ch in enumerate(channels[:MAX_CHANNEL_CHOICES], 1):
        lines.append(f"{i}. {_channel_label(ch)}")
    lines.append("")
    lines.append("Нажми кнопку или отправь номер канала.")
    _send(token, chat_id, "\n".join(lines), reply_markup=_channel_keyboard(channels))


def handle(
    token: str,
    chat_id: int,
    user_id: int,
    text: str,
    clients_db: ClientsDb,
    ruch_db: RuchDb,
    raw_message: dict | None = None,
) -> bool:
    """Обработать сообщение в контексте ручного постинга.

    Возвращает True, если сообщение поглощено диалогом.
    """
    cmd = text.strip().lower()
    draft = ruch_db.get_draft(user_id)
    state = (draft or {}).get("state", STATE_IDLE)

    # --- глобальные команды раздела ---
    if cmd in ("/cancel", "отмена", "❌ отмена"):
        ruch_db.clear_draft(user_id)
        _send(token, chat_id, "❌ Отменено.")
        return True

    if cmd in ("/post", "✍️ новый пост", "новый пост"):
        if state.startswith("repeating:"):
            _send(token, chat_id,
                  "⏳ Сначала заверши создание повторяющегося сообщения\n"
                  "(отправь его текст/время/дни или `/cancel`).")
            return True
        start(token, chat_id, user_id, clients_db, ruch_db)
        return True

    if cmd in ("/posts", "📋 мои посты", "мои посты"):
        _show_posts(token, chat_id, user_id, ruch_db)
        return True

    if cmd in ("/del_last", "🗑 удалить последний", "удалить последний"):
        result = manual_post.delete_last_post(ruch_db, user_id, token)
        if result["ok"]:
            _send(token, chat_id, f"🗑 Последний пост удалён (message_id={result['message_id']}).")
        else:
            _send(token, chat_id, f"❌ {result.get('error', 'не удалось удалить')}")
        return True

    if cmd in ("/reserve", "⏳ в резерв", "в резерв"):
        _to_reserve(token, chat_id, user_id, ruch_db)
        return True

    # --- состояния ---
    if state == STATE_CHANNEL:
        return _on_channel(token, chat_id, user_id, text, clients_db, ruch_db)

    if state == STATE_CONTENT:
        return _on_content(token, chat_id, user_id, text, ruch_db, raw_message)

    if state == STATE_CONFIRM:
        return _on_confirm(token, chat_id, user_id, text, ruch_db)

    return False


def _on_channel(
    token: str, chat_id: int, user_id: int, text: str,
    clients_db: ClientsDb, ruch_db: RuchDb,
) -> bool:
    channels = _manual_channels(clients_db, user_id)
    raw = text.strip()
    try:
        idx = int(raw) - 1
    except ValueError:
        _send(
            token, chat_id,
            "❌ Отправь номер канала из списка или нажми кнопку.",
            reply_markup=_channel_keyboard(channels),
        )
        return True

    if idx < 0 or idx >= min(len(channels), MAX_CHANNEL_CHOICES):
        _send(
            token, chat_id,
            "❌ Нет такого номера.",
            reply_markup=_channel_keyboard(channels),
        )
        return True

    ch = channels[idx]
    ruch_db.save_draft(user_id, state=STATE_CONTENT, channel_chat_id=ch["chat_id"])
    _send(
        token, chat_id,
        f"✅ Канал: {_channel_label(ch)}\n\n"
        "Отправь текст поста. Можно приложить фото, документ или видео — "
        "подпись станет текстом поста.",
        reply_markup=_content_keyboard(),
    )
    return True


def _on_content(
    token: str, chat_id: int, user_id: int, text: str,
    ruch_db: RuchDb, raw_message: dict | None,
) -> bool:
    media_type = "text"
    media_path = None
    caption = text

    if raw_message:
        photo = raw_message.get("photo")
        document = raw_message.get("document")
        video = raw_message.get("video")
        if photo:
            media_type = "photo"
            media_path = photo[-1].get("file_id")
        elif video:
            media_type = "video"
            media_path = video.get("file_id")
        elif document:
            media_type = "document"
            media_path = document.get("file_id")
        if media_type != "text":
            caption = raw_message.get("caption") or ""

    if not caption.strip() and media_type == "text":
        _send(
            token, chat_id,
            "❌ Пустой текст. Отправь текст поста или медиа с подписью.",
            reply_markup=_content_keyboard(),
        )
        return True

    ruch_db.save_draft(
        user_id, state=STATE_CONFIRM, text=caption,
        media_type=media_type, media_path=media_path,
    )
    draft = ruch_db.get_draft(user_id) or {}
    preview = caption[:300] + ("…" if len(caption) > 300 else "")
    media_line = f"\n📎 Медиа: {media_type}" if media_type != "text" else ""
    _send(
        token, chat_id,
        f"👀 **Проверь пост**\n\n"
        f"Канал: `{draft.get('channel_chat_id')}`{media_line}\n\n"
        f"---\n{preview}\n---",
        reply_markup=_confirm_keyboard(),
    )
    return True


def _on_confirm(
    token: str, chat_id: int, user_id: int, text: str, ruch_db: RuchDb,
) -> bool:
    cmd = text.strip().lower()

    if cmd in ("/send", "отправить", "✅ отправить"):
        draft = ruch_db.get_draft(user_id) or {}
        post_id = draft.get("post_id")
        if not post_id:
            post_id = ruch_db.add_manual_post(
                user_id=user_id,
                channel_chat_id=draft["channel_chat_id"],
                text=draft.get("text") or "",
                media_type=draft.get("media_type") or "text",
                media_path=draft.get("media_path"),
                parse_mode=draft.get("parse_mode") or "HTML",
                reply_to_msg_id=draft.get("reply_to_msg_id"),
            )
            ruch_db.save_draft(user_id, post_id=post_id)

        result = manual_post.publish_post(ruch_db, post_id, token)
        if result["ok"]:
            ruch_db.clear_draft(user_id)
            _send(
                token, chat_id,
                f"✅ Опубликовано (message_id={result['message_id']}).",
                reply_markup=inline_keyboard([
                    [("✍️ Новый пост", f"{CB_PREFIX}/post"),
                     ("📋 Мои посты", f"{CB_PREFIX}/posts")],
                ]),
            )
        else:
            _send(
                token, chat_id,
                f"❌ Не отправлено: {result.get('error')}",
                reply_markup=inline_keyboard([
                    [("🔁 Повторить", f"{CB_PREFIX}/send"),
                     ("❌ Отмена", f"{CB_PREFIX}/cancel")],
                ]),
            )
        return True

    if cmd in ("/edit", "изменить", "✏️ изменить"):
        ruch_db.save_draft(user_id, state=STATE_CONTENT, text="", post_id=None)
        _send(
            token, chat_id,
            "✏️ Отправь новый текст поста.",
            reply_markup=_content_keyboard(),
        )
        return True

    _send(
        token, chat_id,
        "Выбери действие:",
        reply_markup=_confirm_keyboard(),
    )
    return True


def _show_posts(token: str, chat_id: int, user_id: int, ruch_db: RuchDb) -> None:
    posts = manual_post.list_posts(ruch_db, user_id, limit=POSTS_PAGE)
    if not posts:
        _send(
            token, chat_id,
            "📋 Постов пока нет.",
            reply_markup=inline_keyboard([[("✍️ Новый пост", f"{CB_PREFIX}/post")]]),
        )
        return
    icon = {"sent": "✅", "draft": "📝", "failed": "❌"}
    lines = ["📋 **Последние посты**\n"]
    for p in posts:
        mark = icon.get(p["status"], "•")
        preview = (p.get("text") or "")[:60].replace("\n", " ")
        lines.append(f"{mark} `#{p['id']}` → `{p['channel_chat_id']}` — {preview}")
    _send(token, chat_id, "\n".join(lines), reply_markup=_posts_keyboard())


def _to_reserve(token: str, chat_id: int, user_id: int, ruch_db: RuchDb) -> None:
    """Отложить текущий черновик в rezerv_post."""
    from rezerv_post.rezerv_db import ReserveDb

    draft = ruch_db.get_draft(user_id) or {}
    if not draft.get("text") and not draft.get("media_path"):
        _send(token, chat_id, "❌ Нечего откладывать — сначала набери пост.")
        return
    if not draft.get("channel_chat_id"):
        _send(token, chat_id, "❌ Сначала выбери канал.")
        return

    reserve = ReserveDb()
    try:
        rid = reserve.add(
            user_id=user_id,
            text=draft.get("text") or "",
            source="manual",
            media_type=draft.get("media_type") or "text",
            media_path=draft.get("media_path"),
            parse_mode=draft.get("parse_mode") or "HTML",
        )
    finally:
        reserve.close()

    ruch_db.clear_draft(user_id)
    _send(
        token, chat_id,
        f"⏳ Пост отложен в резерв (id={rid}, TTL 48ч).",
        reply_markup=inline_keyboard([[("✍️ Новый пост", f"{CB_PREFIX}/post")]]),
    )
