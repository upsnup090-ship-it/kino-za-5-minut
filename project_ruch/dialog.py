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
#: Публикация отложенного поста: заново выбираем канал.
STATE_RESERVE_CHANNEL = "await_reserve_channel"

#: Сколько каналов показывать в выборе.
MAX_CHANNEL_CHOICES = 10

#: Сколько постов показывать в /posts.
POSTS_PAGE = 10

#: Сколько отложенных постов показывать в /reserve.
RESERVE_PAGE = 10

#: Префикс callback_data кнопок диалога.
CB_PREFIX = "cb:"

#: Кнопка выхода в главное меню (есть на каждом экране диалога).
MENU_BUTTON = ("🏠 В меню", f"{CB_PREFIX}/menu")


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
    rows.append([("❌ Отмена", f"{CB_PREFIX}/cancel"), MENU_BUTTON])
    return inline_keyboard(rows)


def _confirm_keyboard() -> dict:
    return inline_keyboard([
        [("✅ Отправить", f"{CB_PREFIX}/send"), ("✏️ Изменить", f"{CB_PREFIX}/edit")],
        [("⏳ В резерв", f"{CB_PREFIX}/reserve"), ("❌ Отмена", f"{CB_PREFIX}/cancel")],
        [MENU_BUTTON],
    ])


def _content_keyboard() -> dict:
    return inline_keyboard([
        [("❌ Отмена", f"{CB_PREFIX}/cancel"), MENU_BUTTON],
    ])


def _posts_keyboard() -> dict:
    return inline_keyboard([
        [("✍️ Новый пост", f"{CB_PREFIX}/post"), ("🗑 Удалить последний", f"{CB_PREFIX}/del_last")],
        [("⏳ Резерв", f"{CB_PREFIX}/reserve"), MENU_BUTTON],
    ])


def _posts_actions_keyboard(posts: list[dict[str, Any]]) -> dict:
    """Кнопки действий под списком постов: удалить/переотправить каждый."""
    rows: list[list[tuple[str, str]]] = []
    for p in posts:
        pid = p["id"]
        rows.append([
            (f"🗑 Удалить #{pid}", f"{CB_PREFIX}/post_del_{pid}"),
            (f"🔁 Переотправить #{pid}", f"{CB_PREFIX}/post_resend_{pid}"),
        ])
    rows.append([("✍️ Новый пост", f"{CB_PREFIX}/post"), MENU_BUTTON])
    return inline_keyboard(rows)


def _reserve_keyboard(items: list[dict[str, Any]]) -> dict:
    """Кнопки действий под списком отложенного: опубликовать/удалить каждый."""
    rows: list[list[tuple[str, str]]] = []
    for it in items:
        rid = it["id"]
        rows.append([
            (f"📤 Опубликовать #{rid}", f"{CB_PREFIX}/reserve_pub_{rid}"),
            (f"🗑 Удалить #{rid}", f"{CB_PREFIX}/reserve_del_{rid}"),
        ])
    rows.append([("✍️ Новый пост", f"{CB_PREFIX}/post"), MENU_BUTTON])
    return inline_keyboard(rows)


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

    # Выход в главное меню: черновик НЕ сбрасываем — клиент может вернуться.
    if cmd in ("/menu", "🏠 в меню", "в меню", "меню"):
        _send(
            token, chat_id,
            "🏠 Возврат в главное меню. Черновик сохранён — `/post` вернёт к нему.",
        )
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

    # --- резерв: список отложенного (кнопка «⏳ Резерв» и /reserve) ---
    if cmd in ("/reserve", "⏳ резерв", "резерв", "⏳ в резерв", "в резерв"):
        # Внутри подтверждения «В резерв» — отложить текущий черновик.
        if state == STATE_CONFIRM:
            _to_reserve(token, chat_id, user_id, ruch_db)
            return True
        _show_reserve(token, chat_id, user_id)
        return True

    # --- действия над конкретным постом: /post_del_<id>, /post_resend_<id> ---
    if cmd.startswith("/post_del_"):
        _delete_post_by_id(token, chat_id, user_id, cmd, ruch_db)
        return True

    if cmd.startswith("/post_resend_"):
        _resend_post_by_id(token, chat_id, user_id, cmd, ruch_db)
        return True

    # --- действия над отложенным: /reserve_pub_<id>, /reserve_del_<id> ---
    if cmd.startswith("/reserve_pub_"):
        _reserve_publish_start(token, chat_id, user_id, cmd, clients_db, ruch_db)
        return True

    if cmd.startswith("/reserve_del_"):
        _reserve_delete(token, chat_id, user_id, cmd)
        return True

    # --- состояния ---
    if state == STATE_CHANNEL:
        return _on_channel(token, chat_id, user_id, text, clients_db, ruch_db)

    if state == STATE_RESERVE_CHANNEL:
        return _on_reserve_channel(token, chat_id, user_id, text, clients_db, ruch_db)

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
    """Список последних постов + кнопки действий над каждым."""
    posts = manual_post.list_posts(ruch_db, user_id, limit=POSTS_PAGE)
    if not posts:
        _send(
            token, chat_id,
            "📋 **Мои посты**\n\n"
            "Постов пока нет.\n\n"
            "Нажми «✍️ Новый пост», чтобы создать первый.",
            reply_markup=_posts_keyboard(),
        )
        return
    icon = {"sent": "✅", "draft": "📝", "failed": "❌"}
    lines = ["📋 **Мои посты** (последние %d)\n" % len(posts)]
    for p in posts:
        mark = icon.get(p["status"], "•")
        preview = (p.get("text") or "")[:60].replace("\n", " ")
        media = f" 📎{p['media_type']}" if p.get("media_type") not in (None, "text") else ""
        lines.append(
            f"{mark} `#{p['id']}` → `{p['channel_chat_id']}`{media} — {preview}"
        )
    lines.append("")
    lines.append("Действия — кнопками ниже.")
    _send(token, chat_id, "\n".join(lines), reply_markup=_posts_actions_keyboard(posts))


def _show_reserve(token: str, chat_id: int, user_id: int) -> None:
    """Список отложенных постов + кнопки «Опубликовать»/«Удалить»."""
    from project_ruch import reserve as reserve_mod

    items = reserve_mod.list_pending(user_id)
    if not items:
        _send(
            token, chat_id,
            "⏳ **Резерв**\n\n"
            "Отложенных постов нет.\n\n"
            "Отложить можно на шаге подтверждения поста — кнопка «⏳ В резерв».\n"
            "Отложенное хранится 48 часов.",
            reply_markup=_posts_keyboard(),
        )
        return

    lines = [f"⏳ **Резерв** — отложено {len(items)}\n"]
    for it in items[:RESERVE_PAGE]:
        preview = (it.get("text") or "")[:60].replace("\n", " ")
        media = f" 📎{it['media_type']}" if it.get("media_type") not in (None, "text") else ""
        expires = (it.get("expires_at") or "")[:16].replace("T", " ")
        lines.append(f"• `#{it['id']}`{media} — {preview}")
        lines.append(f"    ⏱ до {expires}")
    lines.append("")
    lines.append("📤 «Опубликовать» — спрошу канал и отправлю.")
    lines.append("🗑 «Удалить» — убрать из резерва.")
    _send(
        token, chat_id, "\n".join(lines),
        reply_markup=_reserve_keyboard(items[:RESERVE_PAGE]),
    )


def _parse_id(cmd: str, prefix: str) -> int | None:
    """Достать числовой id из команды вида /prefix_<id>."""
    raw = cmd[len(prefix):].strip()
    return int(raw) if raw.isdigit() else None


def _delete_post_by_id(
    token: str, chat_id: int, user_id: int, cmd: str, ruch_db: RuchDb,
) -> None:
    """Удалить конкретный пост по id (из канала и из БД)."""
    from pro_code.tg_api import delete_message

    post_id = _parse_id(cmd, "/post_del_")
    if post_id is None:
        _send(token, chat_id, "❌ Не понял номер поста.")
        return

    post = ruch_db.get_post(post_id)
    if not post or int(post["user_id"]) != user_id:
        _send(token, chat_id, f"❌ Пост `#{post_id}` не найден.")
        return

    msg_id = ruch_db.get_message_id(post_id)
    deleted = False
    if msg_id:
        deleted = delete_message(token, post["channel_chat_id"], msg_id)
    ruch_db.delete_post(post_id)

    if msg_id and not deleted:
        _send(
            token, chat_id,
            f"🗑 Пост `#{post_id}` удалён из базы.\n"
            "⚠️ Из канала удалить не удалось (нет прав или сообщение уже удалено).",
            reply_markup=_posts_keyboard(),
        )
    else:
        _send(
            token, chat_id,
            f"🗑 Пост `#{post_id}` удалён.",
            reply_markup=_posts_keyboard(),
        )


def _resend_post_by_id(
    token: str, chat_id: int, user_id: int, cmd: str, ruch_db: RuchDb,
) -> None:
    """Переотправить конкретный пост в его канал."""
    post_id = _parse_id(cmd, "/post_resend_")
    if post_id is None:
        _send(token, chat_id, "❌ Не понял номер поста.")
        return

    post = ruch_db.get_post(post_id)
    if not post or int(post["user_id"]) != user_id:
        _send(token, chat_id, f"❌ Пост `#{post_id}` не найден.")
        return

    # Новый черновик из старого поста — публикуем заново.
    new_id = ruch_db.add_manual_post(
        user_id=user_id,
        channel_chat_id=post["channel_chat_id"],
        text=post.get("text") or "",
        media_type=post.get("media_type") or "text",
        media_path=post.get("media_path"),
        parse_mode=post.get("parse_mode") or "HTML",
    )
    result = manual_post.publish_post(ruch_db, new_id, token)
    if result.get("ok"):
        _send(
            token, chat_id,
            f"🔁 Пост `#{post_id}` переотправлен "
            f"(новый `#{new_id}`, message_id={result.get('message_id')}).",
            reply_markup=_posts_keyboard(),
        )
    else:
        _send(
            token, chat_id,
            f"❌ Не удалось переотправить `#{post_id}`: {result.get('error')}",
            reply_markup=_posts_keyboard(),
        )


def _reserve_publish_start(
    token: str, chat_id: int, user_id: int, cmd: str,
    clients_db: ClientsDb, ruch_db: RuchDb,
) -> None:
    """Кнопка «Опубликовать #N»: спросить канал и запомнить, что публикуем."""
    from project_ruch import reserve as reserve_mod

    reserve_id = _parse_id(cmd, "/reserve_pub_")
    if reserve_id is None:
        _send(token, chat_id, "❌ Не понял номер отложенного поста.")
        return

    items = reserve_mod.list_pending(user_id)
    item = next((i for i in items if int(i["id"]) == reserve_id), None)
    if item is None:
        _send(
            token, chat_id,
            f"❌ Отложенный пост `#{reserve_id}` не найден или истёк.",
            reply_markup=_posts_keyboard(),
        )
        return

    channels = _manual_channels(clients_db, user_id)
    if not channels:
        _send(
            token, chat_id,
            "❌ Нет каналов для ручного постинга.\n"
            "Перешли пост из своего канала или `/add_manual <chat_id>`.",
        )
        return

    # Запоминаем, какой отложенный пост публикуем (в extra черновика).
    import json as _json
    ruch_db.save_draft(
        user_id,
        state=STATE_RESERVE_CHANNEL,
        channel_chat_id=None,
        text="",
        media_type="text",
        media_path=None,
        post_id=None,
        extra=_json.dumps({"reserve_id": reserve_id}),
    )

    preview = (item.get("text") or "")[:200]
    lines = [
        f"📤 **Публикация отложенного `#{reserve_id}`**\n",
        f"---\n{preview}\n---\n",
        "Куда публикуем?\n",
    ]
    for i, ch in enumerate(channels[:MAX_CHANNEL_CHOICES], 1):
        lines.append(f"{i}. {_channel_label(ch)}")
    lines.append("")
    lines.append("Нажми кнопку или отправь номер канала.")
    _send(token, chat_id, "\n".join(lines), reply_markup=_channel_keyboard(channels))


def _on_reserve_channel(
    token: str, chat_id: int, user_id: int, text: str,
    clients_db: ClientsDb, ruch_db: RuchDb,
) -> bool:
    """Выбор канала для публикации отложенного поста."""
    import json as _json
    from project_ruch import reserve as reserve_mod

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

    draft = ruch_db.get_draft(user_id) or {}
    try:
        reserve_id = int(_json.loads(draft.get("extra") or "{}").get("reserve_id"))
    except (TypeError, ValueError):
        ruch_db.clear_draft(user_id)
        _send(token, chat_id, "❌ Потерял, что публикуем. Открой `/reserve` заново.")
        return True

    ch = channels[idx]
    result = reserve_mod.publish_by_id(
        ruch_db, user_id, token, ch["chat_id"], reserve_id,
    )
    ruch_db.clear_draft(user_id)

    if result.get("ok"):
        _send(
            token, chat_id,
            f"✅ Отложенный `#{reserve_id}` опубликован в {_channel_label(ch)}\n"
            f"(message_id={result.get('message_id')}).",
            reply_markup=_posts_keyboard(),
        )
    else:
        _send(
            token, chat_id,
            f"❌ Не опубликовано: {result.get('error')}",
            reply_markup=_posts_keyboard(),
        )
    return True


def _reserve_delete(token: str, chat_id: int, user_id: int, cmd: str) -> None:
    """Кнопка «Удалить #N»: убрать отложенный пост из резерва."""
    from project_ruch.rezerv_post.rezerv_db import ReserveDb

    reserve_id = _parse_id(cmd, "/reserve_del_")
    if reserve_id is None:
        _send(token, chat_id, "❌ Не понял номер отложенного поста.")
        return

    reserve = ReserveDb()
    try:
        item = reserve.get(reserve_id)
        if not item or int(item.get("user_id")) != user_id:
            _send(
                token, chat_id,
                f"❌ Отложенный пост `#{reserve_id}` не найден.",
                reply_markup=_posts_keyboard(),
            )
            return
        reserve.delete(reserve_id)
    finally:
        reserve.close()

    _send(
        token, chat_id,
        f"🗑 Отложенный пост `#{reserve_id}` удалён из резерва.",
        reply_markup=_posts_keyboard(),
    )


def _to_reserve(token: str, chat_id: int, user_id: int, ruch_db: RuchDb) -> None:
    """Отложить текущий черновик в rezerv_post."""
    from project_ruch.rezerv_post.rezerv_db import ReserveDb

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
