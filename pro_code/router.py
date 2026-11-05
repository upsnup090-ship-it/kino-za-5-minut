"""Маршрутизация апдейтов от ботов клиентов по модулям.

Диспетчер получает апдейт → router определяет user_id и план → направляет
в нужный модуль или возвращает экран-заглушку.
"""

from __future__ import annotations

import logging
from typing import Any

from pro_code.clients_db import ClientsDb
from pro_code.info import ABOUT_TEXT, FAQ_TEXT
from pro_code.slots_ui import handle_slots_command
from pro_code.tg_api import inline_keyboard, send_message
from pro_code import backup_ui, repeating_ui, stats_ui
from project_ruch import dialog as manual_dialog
from project_ruch.ruch_db import RuchDb
from rrA.admin_router import handle_owner_command
from pro_code.kino_auto_db import KinoAutoDb

logger = logging.getLogger(__name__)

#: Заглушки для модулей (пока не реализованы)
MODULES_PLACEHOLDER = {
    "autoposting": (
        "🤖 **Автопостинг** (план auto)\n\n"
        "📢 `/channels` — каналы (назначение 🤖 автопостинг)\n"
        "🗓 `/slots` — расписание слотов\n"
        "• `/slot add пн 20:50` — добавить слот\n"
        "• `/slot del <id>` — удалить\n"
        "• `/slot on/off <id>` — вкл/выкл\n\n"
        "В каждом слоте бот публикует один пост во все твои\n"
        "каналы с назначением автопостинг."
    ),
}


def menu_keyboard(plan: str, is_owner: bool = False) -> dict:
    """Inline-кнопки главного меню по плану (baseline §4, §5)."""
    rows: list[list[tuple[str, str]]] = [
        [("🤖 Автопостинг / слоты", "cb:/slots")] if plan == "auto" else [],
        [("✍️ Ручной постинг", "cb:/post_main")],
        [("📊 Статистика", "cb:/stats"), ("📢 Мои каналы", "cb:/channels")],
        [("ℹ️ Инфо", "cb:/info")],
    ]
    if is_owner:
        rows.append([("🛠 Админ-панель", "cb:/admin")])
    return inline_keyboard([r for r in rows if r])


def _post_main_keyboard() -> dict:
    """Подменю «Ручной постинг»: 5 действий + «В меню»."""
    return inline_keyboard([
        [("🔁 Повторяющиеся", "cb:/repeat"), ("📝 Сделать пост", "cb:/post")],
        [("📋 Мои посты", "cb:/posts"), ("⏳ Резерв", "cb:/reserve")],
        [("💾 Бэкап", "cb:/backup")],
        [("🏠 В меню", "cb:/menu")],
    ])


def build_main_menu(plan: str, is_owner: bool = False) -> str:
    """Адаптивное главное меню: текст + inline-кнопки (см. menu_keyboard)."""
    lines = ["🏠 **Главное меню**\n"]
    lines.append("Выбирай кнопку ниже или команду:")
    if plan == "auto":
        lines.append("• `/slots` — слоты автопостинга")
    lines.append("• `✍️ Ручной постинг` — подменю (повторы, пост, резерв, бэкап)")
    lines.append("• `/stats` — статистика")
    lines.append("• `/channels` — мои каналы")
    lines.append("• `/info` — о боте")
    if is_owner:
        lines.append("• `/admin` — админ-панель")
    return "\n".join(lines)


def _handle_forwarded_message(
    token: str,
    chat_id: int,
    user_id: int,
    plan: str,
    raw_message: dict,
    clients_db: ClientsDb,
) -> bool:
    """Обработать пересланное сообщение из канала/чата/группы.

    Работает одинаково для free и auto:
      — канал (channel) → можно публиковать посты
      — супергруппа/группа (supergroup/group) → можно отправлять сообщения

    Возвращает True, если сообщение было пересланным и обработано.
    """
    fwd_chat = raw_message.get("forward_from_chat")
    if not fwd_chat:
        return False

    ch_id = fwd_chat.get("id")
    ch_type = fwd_chat.get("type", "channel")
    ch_title = fwd_chat.get("title", "чат/канал")

    if not ch_id:
        return False

    # Приводим тип к нашим константам
    type_map = {"channel": "channel", "supergroup": "supergroup", "group": "chat"}
    chat_type = type_map.get(ch_type, ch_type)

    # Человеческое название типа
    type_label = {
        "channel": "📢 канал",
        "supergroup": "👥 группа",
        "chat": "👥 группа",
    }.get(chat_type, ch_type)

    existing = clients_db.get_channel(user_id, ch_id)

    if existing:
        purpose_emoji = {"auto": "🤖", "manual": "✍️", "both": "🤖✍️"}
        emoji = purpose_emoji.get(existing["purpose"], "❓")
        send_message(
            token, chat_id,
            f"📢 **{type_label} «{ch_title}» (`{ch_id}`)** уже добавлен "
            f"({emoji} {existing['purpose']}).\n\n"
            "Чтобы сменить назначение — отправь:\n"
            f"`/channel_purpose {ch_id} auto`\n"
            f"`/channel_purpose {ch_id} manual`\n"
            f"`/channel_purpose {ch_id} both`\n\n"
            "Чтобы удалить — `/remove_channel {ch_id}`",
        )
        return True

    # Новый чат — определяем доступные назначения
    # auto: auto/manual/both; free: только manual
    if plan == "auto":
        send_message(
            token, chat_id,
            f"📢 **Обнаружен {type_label} «{ch_title}» (`{ch_id}`)**\n\n"
            "Для чего его использовать?\n\n"
            f"📍 `/add_auto {ch_id}` — для автопостинга (🤖)\n"
            f"📍 `/add_manual {ch_id}` — для ручного постинга (✍️)\n"
            f"📍 `/add_both {ch_id}` — для обоих",
        )
    else:
        # free — только ручной
        clients_db.add_channel(user_id, ch_id, ch_title, chat_type, purpose="manual")
        send_message(
            token, chat_id,
            f"✅ **{type_label} «{ch_title}» (`{ch_id}`)** добавлен для "
            f"**ручного постинга** (✍️).\n\n"
            "Теперь ты можешь создавать посты в этот чат/канал.",
        )

    return True


def _show_channels(token: str, chat_id: int, user_id: int, clients_db: ClientsDb) -> None:
    """Показать список каналов/чатов клиента."""
    channels = clients_db.list_channels(user_id)
    if not channels:
        send_message(
            token, chat_id,
            "📢 **У тебя пока нет добавленных каналов или чатов.**\n\n"
            "Перешли любой пост из своего канала или группы сюда — я запомню его.\n\n"
            "После этого ты сможешь создавать посты в этот чат/канал.",
        )
        return

    purpose_emoji = {"auto": "🤖", "manual": "✍️", "both": "🤖✍️"}
    type_icon = {"channel": "📢", "supergroup": "👥", "group": "👥", "chat": "👥"}
    lines = ["📢 **Мои каналы и чаты**\n"]
    for ch in channels:
        emoji = purpose_emoji.get(ch["purpose"], "❓")
        ticon = type_icon.get(ch["chat_type"], "💬")
        title = ch["title"] or f"чат {ch['chat_id']}"
        lines.append(f"{ticon} {emoji} `{ch['chat_id']}` — {title} ({ch['purpose']})")
    lines.append("")
    lines.append("ℹ️ Чтобы добавить — перешли пост из канала или группы сюда.")
    lines.append("🗑 Чтобы удалить — `/remove_channel {chat_id}`")
    lines.append("🔄 Чтобы сменить — `/channel_purpose {chat_id} auto|manual|both`")
    send_message(token, chat_id, "\n".join(lines))


def handle_message(
    token: str,
    user_id: int,
    plan: str,
    is_owner: bool,
    chat_id: int,
    text: str,
    clients_db: ClientsDb | None = None,
    ruch_db: RuchDb | None = None,
    raw_message: dict | None = None,
) -> None:
    """Обработать текстовое сообщение от клиента."""
    cmd = text.strip().lower()

    # --- пересланное сообщение из канала/чата ---
    if raw_message and clients_db:
        if _handle_forwarded_message(token, chat_id, user_id, plan, raw_message, clients_db):
            return

    # --- команды навигации (приоритет: работают всегда, даже внутри диалога) ---
    if cmd == "/start":
        reply = (
            "👋 Привет!\n\n"
            "Твой бот активирован. Используй меню ниже.\n\n"
            + build_main_menu(plan, is_owner)
        )
        send_message(token, chat_id, reply, reply_markup=menu_keyboard(plan, is_owner))
        return

    if cmd in ("/menu", "/help"):
        if ruch_db:
            ruch_db.clear_draft(user_id)
        send_message(token, chat_id, build_main_menu(plan, is_owner), reply_markup=menu_keyboard(plan, is_owner))
        return

    if cmd == "/post_main":
        if not clients_db or not ruch_db:
            send_message(token, chat_id, "✍️ **Ручной постинг**\n\nМодуль недоступен.")
            return
        send_message(token, chat_id, "📝 **Ручной постинг**\n\nВыбирай действие:", reply_markup=_post_main_keyboard())
        return

    # --- диалог повторяющихся сообщений (поглощает, если активен) ---
    if ruch_db and clients_db:
        if repeating_ui.handle_add_dialog(token, chat_id, user_id, text, clients_db, ruch_db):
            return

    # --- диалог ручного постинга (поглощает сообщение, если он активен) ---
    if ruch_db and clients_db:
        if manual_dialog.handle(
            token, chat_id, user_id, text, clients_db, ruch_db, raw_message=raw_message
        ):
            return

    # --- слоты автопостинга (план auto) ---
    if clients_db and (cmd.startswith("/slot") or "слот" in cmd):
        auto_db = KinoAutoDb()
        if handle_slots_command(token, chat_id, user_id, text, clients_db, auto_db, plan):
            return

    # --- активация ключом Auto: /activation <ключ> ---
    if clients_db and cmd.startswith("/activation"):
        _activate_key(token, chat_id, user_id, text, clients_db)
        return

    # /admin — только для владельца диспетчера: вход в админ-панель.
    if cmd in ("/admin", "🛠 админ-панель", "админ"):
        if not is_owner:
            send_message(token, chat_id, "❌ Админ-панель доступна только владельцу.")
            return
        if clients_db and ruch_db:
            handle_owner_command(token, chat_id, "/menu", clients_db, ruch_db)
        return

    # --- команды owner перенаправляем в admin_router ---
    # /admin — явный вход в админ-панель (RootA) для владельца.
    if is_owner:
        from rrA.admin_router import OWNER_COMMANDS
        if any(cmd.startswith(c + " ") or cmd == c for c in OWNER_COMMANDS) \
                or cmd in ("/help", "/admin"):
            if clients_db and ruch_db:
                if cmd == "/admin":
                    handle_owner_command(token, chat_id, "/menu", clients_db, ruch_db)
                else:
                    handle_owner_command(token, chat_id, text, clients_db, ruch_db)
                return

    if cmd == "/info":
        send_message(token, chat_id, ABOUT_TEXT)
        return

    if cmd == "/faq":
        send_message(token, chat_id, FAQ_TEXT)
        return

    # --- управление каналами ---
    if cmd in ("📢 мои каналы", "мои каналы", "/channels", "📢"):
        if clients_db:
            _show_channels(token, chat_id, user_id, clients_db)
        return

    if cmd.startswith("/add_auto ") and clients_db:
        # /add_auto <chat_id>
        args = cmd[10:].strip()
        try:
            ch_id = int(args)
        except ValueError:
            send_message(token, chat_id, "❌ Укажи ID канала числом.")
            return
        # Берём из БД если уже был добавлен через пересылку, иначе создаём
        existing = clients_db.get_channel(user_id, ch_id)
        if existing:
            clients_db.set_channel_purpose(user_id, ch_id, "auto")
        else:
            clients_db.add_channel(user_id, ch_id, "", "channel", purpose="auto")
        # Также обновляем auto_channel_id
        clients_db.set_auto_channel(user_id, ch_id)
        send_message(token, chat_id, f"✅ Канал `{ch_id}` назначен для **автопостинга** (🤖).")
        return

    if cmd.startswith("/add_manual ") and clients_db:
        args = cmd[12:].strip()
        try:
            ch_id = int(args)
        except ValueError:
            send_message(token, chat_id, "❌ Укажи ID канала числом.")
            return
        existing = clients_db.get_channel(user_id, ch_id)
        if existing:
            clients_db.set_channel_purpose(user_id, ch_id, "manual")
        else:
            clients_db.add_channel(user_id, ch_id, "", "channel", purpose="manual")
        send_message(token, chat_id, f"✅ Канал `{ch_id}` назначен для **ручного постинга** (✍️).")
        return

    if cmd.startswith("/add_both ") and clients_db:
        args = cmd[10:].strip()
        try:
            ch_id = int(args)
        except ValueError:
            send_message(token, chat_id, "❌ Укажи ID канала числом.")
            return
        existing = clients_db.get_channel(user_id, ch_id)
        if existing:
            clients_db.set_channel_purpose(user_id, ch_id, "both")
        else:
            clients_db.add_channel(user_id, ch_id, "", "channel", purpose="both")
        clients_db.set_auto_channel(user_id, ch_id)
        send_message(token, chat_id, f"✅ Канал `{ch_id}` назначен для **авто и ручного постинга** (🤖✍️).")
        return

    if cmd.startswith("/remove_channel ") and clients_db:
        args = cmd[16:].strip()
        try:
            ch_id = int(args)
        except ValueError:
            send_message(token, chat_id, "❌ Укажи ID канала числом.")
            return
        if clients_db.remove_channel(user_id, ch_id):
            # Если это был auto_channel — сбрасываем
            if clients_db.get_auto_channel(user_id) == ch_id:
                clients_db.set_auto_channel(user_id, None)
            send_message(token, chat_id, f"🗑 Канал `{ch_id}` удалён.")
        else:
            send_message(token, chat_id, f"❌ Канал `{ch_id}` не найден.")
        return

    if cmd.startswith("/channel_purpose ") and clients_db:
        # /channel_purpose <chat_id> auto|manual|both
        args = cmd[17:].strip()
        parts = args.split(None, 1)
        if len(parts) < 2:
            send_message(token, chat_id, "❌ Формат: `/channel_purpose {chat_id} auto|manual|both`")
            return
        try:
            ch_id = int(parts[0])
        except ValueError:
            send_message(token, chat_id, "❌ ID канала должен быть числом.")
            return
        purpose = parts[1].strip().lower()
        if purpose not in ("auto", "manual", "both"):
            send_message(token, chat_id, "❌ Назначение: auto | manual | both")
            return
        if clients_db.set_channel_purpose(user_id, ch_id, purpose):
            if purpose in ("auto", "both"):
                clients_db.set_auto_channel(user_id, ch_id)
            send_message(token, chat_id, f"✅ Назначение канала `{ch_id}` изменено на **{purpose}**.")
        else:
            send_message(token, chat_id, f"❌ Канал `{ch_id}` не найден. Перешли пост из него сначала.")
        return

    if cmd == "/auto_channel":
        if plan != "auto":
            send_message(token, chat_id, "❌ Автопостинг доступен только по плану **auto**.")
            return
        if clients_db:
            ch = clients_db.get_auto_channel(user_id)
            if ch:
                send_message(token, chat_id, f"🤖 **Канал автопостинга:** `{ch}`\n\n"
                    "Перешли пост из другого канала чтобы сменить, "
                    "или используй `/channels` для управления.")
            else:
                send_message(token, chat_id, "🤖 **Канал автопостинга не назначен.**\n\n"
                    "Перешли любой пост из твоего канала сюда — "
                    "или `/add_auto {chat_id}` если знаешь ID.")
        return

    # --- модули-заглушки ---
    if cmd in ("/repeat", "/repeats") or cmd.startswith("/repeat "):
        repeating_ui.handle(token, chat_id, user_id, text, clients_db, ruch_db)
        return
    if cmd in ("🔁 повторяющиеся", "повторяющиеся", "repeating", "🔁"):
        repeating_ui.handle(token, chat_id, user_id, "/repeat", clients_db, ruch_db)
        return
    if cmd in ("/stats", "📊 статистика", "статистика", "stats", ""):
        if clients_db:
            stats_ui.send_stats(token, chat_id, user_id, clients_db)
        return
    if cmd in ("✍️ ручной постинг", "ручной постинг", "manual", "✍️", "/post"):
        if clients_db and ruch_db:
            manual_dialog.start(token, chat_id, user_id, clients_db, ruch_db)
        else:
            send_message(token, chat_id, "✍️ **Ручной постинг**\n\nМодуль недоступен.")
        return
    if cmd in ("ℹ️ инфо", "инфо", "info", "ℹ️"):
        send_message(token, chat_id, ABOUT_TEXT)
        return
    if cmd in ("🤖 автопостинг", "автопостинг", "autoposting", "🤖"):
        if plan != "auto":
            send_message(token, chat_id, "❌ Автопостинг доступен только по плану **auto**.")
            return
        send_message(token, chat_id, MODULES_PLACEHOLDER["autoposting"])
        return
    if cmd in ("👥 roota", "roota", "клиенты", "👥"):
        if not is_owner:
            send_message(token, chat_id, "❌ Нет доступа.")
            return
        if clients_db and ruch_db:
            handle_owner_command(token, chat_id, "/menu", clients_db, ruch_db)
        return

    # --- неизвестная команда ---
    send_message(
        token, chat_id,
        "Я понимаю только команды из меню. Отправь /menu.",
    )


def _activate_key(
    token: str, chat_id: int, user_id: int, cmd: str, clients_db: ClientsDb,
) -> None:
    """Активация Auto по ключу: /activation <ключ>.

    Ключ генерирует админ (RootA /key <user_id>) и выдаёт лично. Ключ
    многоразовый и привязан к одному клиенту. Ввод совпавшего ключа
    переводит клиента на план auto.
    """
    raw = cmd.split(None, 1)
    if len(raw) < 2 or not raw[1].strip():
        send_message(
            token, chat_id,
            "🔑 **Активация Auto**\n\n"
            "Введи ключ, который выдал тебе мастер:\n"
            "`/activation <ключ>`\n\n"
            "Ключ включает автопостинг кино-новостей и расписание слотов.\n"
            "Free-функции (ручной постинг, повторы, статистика) работают и без ключа.",
        )
        return

    key = raw[1].strip().upper()
    if not key.startswith("KINO-"):
        send_message(
            token, chat_id,
            "❌ Это не похоже на ключ активации. Ключ выглядит так:\n"
            "`KINO-XXXX-XXXX-XXXX`\n\n"
            "Уточни ключ у мастера.",
        )
        return

    owner = clients_db.get_key_user(key)
    if owner is None:
        send_message(token, chat_id, "❌ Такого ключа нет. Проверь ключ и попробуй ещё раз.")
        return
    if owner != user_id:
        send_message(
            token, chat_id,
            "❌ Этот ключ привязан к другому клиенту.\n"
            "Уточни, что получил правильный ключ.",
        )
        return

    # Ключ верный и этого клиента — включаем auto.
    clients_db.upsert_client(user_id, plan="auto", status="active")
    send_message(
        token, chat_id,
        "🚀 **Auto активирован!**\n\n"
        "Теперь доступен автопостинг и расписание слотов.\n\n"
        "• `/slots` — настрой расписание\n"
        "• `/channels` — назначь каналы для автопостинга\n\n"
        "Вызови `/menu`, чтобы обновить меню.",
        reply_markup=menu_keyboard("auto", is_owner=(user_id == int(__import__("os").environ.get("ADMIN_CHAT_ID", "0") or 0))),
    )


def handle_no_token(chat_id: int, token: str | None = None) -> None:
    """Ответ бота клиенту без токена (не активирован)."""
    if token:
        send_message(
            token, chat_id,
            "❌ **Токен не активирован.**\n\n"
            "Для использования бота нужен токен доступа.\n"
            "Обратись к мастеру для получения токена.\n\n"
            "ℹ️ Используй /info для справки о боте.",
        )