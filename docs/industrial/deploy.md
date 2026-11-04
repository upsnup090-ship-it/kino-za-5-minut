# Справочник переменных `.env`

*Обновлено: 2026-11-02. Дополняет `requirements_baseline.md`.*
*Пошаговая инструкция развёртывания — `docs/industrial/RUN.md`.*

---

## 1. Единый файл конфига

```
/workspace/.env
```

Рядом с `pro_code/`, `project_ruch/`, `rrA/`. Закрыт `.gitignore` (`.env*`),
в репозиторий не попадает. Шаблон — `/workspace/.env.example`.

`pro_code/config.py` читает `ROOT/.env`. `kino/config.py` читает тот же файл
через `ENV_ROOT` (`ROOT.parent`). Другого места нет.

## 2. Полный список переменных

### Диспетчер (pro_code)

| Переменная | Обязательна | Что это |
|---|---|---|
| `ADMIN_CHAT_ID` | да | Твой Telegram user_id. Только он видит RootA, бэкап, логи. Узнать: `@userinfobot`. |
| `MASTER_BOT_TOKEN` | нет | Бот мастера (модель A): клиент активирует своего бота через `/start`. |
| `TOKEN_ENCRYPTION_KEY` | нет* | Fernet-ключ; без него токены клиентов из `clients.db` не расшифруются (*обязателен, если хоть один клиент активировался через модель A). |
| `CLIENT_<user_id>` | да, хотя бы одна | Токен бота клиента из `@BotFather`. `<user_id>` — Telegram ID **клиента**, не бота. |
| `CLIENT_<user_id>_PLAN` | нет | `free` (по умолчанию) или `auto`. `auto` открывает автопостинг. |
| `RUCH_TIMEZONE` | нет | Зона повторяющихся сообщений. По умолчанию `Europe/Moscow`. |

### Автопостинг Kino

| Переменная | Обязательна | Что это |
|---|---|---|
| `BOT_TOKEN` | да для автопостинга | Свой бот для канала владельца. |
| `CHANNEL_CHAT_ID` | да для автопостинга | Канал, куда Kino публикует. |
| `LLM_PROVIDER` | да для автопостинга | Провайдер: `openrouter`. |
| `LLM_BASE_URL` | да для автопостинга | Шлюз: `https://ai.reqline.ru/v1`. |
| `OPENROUTER_API_KEY` | да для автопостинга | Ключ API. |
| `DRAFT_MODEL` | нет | Модель черновика; по умолчанию `openai/gpt-4.1-mini`. |
| `JUDGE_MODEL` | нет | Модель судьи; по умолчанию `openai/gpt-4o-mini`. |
| `PROXY_URL` | нет | Прокси для Telegram. |
| `GRAPH_PROXY_URL` | нет | Прокси для graph.org (напрямую из сети сервера не отвечает). |

## 3. Пример

```env
ADMIN_CHAT_ID=123456789
CLIENT_111111111=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
CLIENT_111111111_PLAN=free
RUCH_TIMEZONE=Europe/Moscow
```

## 4. Как клиент получает бота

1. Клиент создаёт бота в `@BotFather` (`/newbot`), получает токен.
2. Присылает тебе `user_id:token` (свой Telegram ID и токен).
3. Ты дописываешь строку `CLIENT_<user_id>=<token>` в `/workspace/.env`.
4. Перезапускаешь диспетчер — бот «оживает».

Смена токена: клиент присылает новый `user_id:token`, ты заменяешь строку,
перезапускаешь. Старый бот перестаёт работать.

## 5. Где лежат данные

| Файл | Что |
|---|---|
| `/workspace/data/clients.db` | клиенты, привязки, каналы, LLM-расход |
| `/workspace/data/ruch.db` | ручной модуль: посты, повторы, статистика, бэкапы, черновики диалога |
| `/workspace/data/rezerv.db` | отложенные посты ручного модуля |
| `/workspace/data/kino_auto.db` | автопостинг диспетчера: per-client слоты, резерв, прогоны |
| `/workspace/project/data/kino.db` | БД мастера автопостинга Kino: слоты, резерв, журнал прогонов |

Все `data/*.db*` закрыты `.gitignore`.

> **Пути выше — песочница (`/workspace`).** На сервере то же самое лежит в
> `/opt/kino/project/data/` и `/opt/kino/project/project/data/kino.db`.

## 6. Чего в `.env` быть не должно

- Токенов в коде, в git, в логах.
- `ADMIN_CHAT_ID` клиента. Мастер — один.
- Паролей и ключей LLM в `CLIENT_*` — они живут в общем файле.