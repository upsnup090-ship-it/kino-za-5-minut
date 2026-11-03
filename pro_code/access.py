"""Проверка доступа: кто имеет право управлять ботом.

Дыра, которую закрывает модуль. Апдейт от бота клиента приходит с любым
``from.id`` — с этим ничего не поделать, бот публичен. Диспетчер же брал
``user_id`` (владельца) из конфига и работал с его данными, вообще не глядя
на отправителя. Любой, кто нашёл ``@client_bot``, мог постить в каналы
клиента, читать его статистику, удалять его посты.

Правило: владелец бота — единственный, кто управляет ботом. Никто другой
(включая мастера в чужом боте) команды не выполняет.

Отдельно мастер-бот: он существует, чтобы клиенты активировали своих
ботов. Поэтому на мастер-боте активация доступна всем, а RootA — только
``admin_chat_id``. Это решает не этот модуль, а ``dispatcher``.
"""

from __future__ import annotations

from typing import Any

#: Текст отказа. Без упоминания внутренностей — постороннему знать нечего.
DENY_TEXT = (
    "🤖 Этот бот не для тебя.\n\n"
    "Если нужен свой бот — обратись к мастеру."
)


def actor_of(update: dict[str, Any]) -> int | None:
    """Кто отправил апдейт: ``from.id`` сообщения или callback_query.

    ``None`` — данных нет (нестандартный апдейт). Отсутствие автора
    трактуется как «не свой»: лучше отказать, чем выполнить чужую команду.
    """
    message = update.get("message")
    if message is not None:
        sender = (message.get("from") or {}).get("id")
        return int(sender) if sender is not None else None
    callback = update.get("callback_query")
    if callback is not None:
        sender = (callback.get("from") or {}).get("id")
        return int(sender) if sender is not None else None
    return None


def is_owner(actor_id: int | None, owner_id: int) -> bool:
    """Имеет ли право отправитель управлять ботом владельца ``owner_id``."""
    return actor_id is not None and actor_id == owner_id


def is_admin(actor_id: int | None, admin_id: int) -> bool:
    """Отправитель — мастер (для RootA и служебных команд)."""
    return actor_id is not None and actor_id == admin_id
