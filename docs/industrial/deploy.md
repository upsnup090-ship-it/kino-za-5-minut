# Боевой запуск — куда вписывать данные

*Обновлено: 2026-11-02. Дополняет `requirements_baseline.md`.*

---

## 1. Один файл конфига

```
/workspace/.env
```

Рядом с `pro_code/`, `project_ruch/`, `rrA/`. Закрыт `.gitignore` (`.env*`),
в репозиторий не попадает. Шаблон — `/workspace/.env.example`.

`pro_code/config.py` читает именно `ROOT/.env`, где `ROOT` — родитель `pro_code/`,
то есть `/workspace`. `kino/config.py` читает тот же файл через `ENV_ROOT`
(`ROOT.parent`). Другого места нет — единый env.

## 2. Что вписать

| Переменная | Обязательна | Что это |
|---|---|---|
| `ADMIN_CHAT_ID` | да | Твой Telegram user_id. Только он видит RootA, бэкап, логи. Узнать: `@userinfobot`. |
| `CLIENT_<user_id>` | да, хотя бы одна | Токен бота клиента из `@BotFather`. `<user_id>` — Telegram ID **клиента**, не бота. |
| `CLIENT_<user_id>_PLAN` | нет | `free` (по умолчанию) или `auto`. `auto` открывает автопостинг. |
| `RUCH_TIMEZONE` | нет | Зона повторяющихся сообщений. По умолчанию `Europe/Moscow`. |

Пример:

```env
ADMIN_CHAT_ID=123456789
CLIENT_111111111=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
CLIENT_111111111_PLAN=free
RUCH_TIMEZONE=Europe/Moscow
```

## 3. Как клиент получает бота

1. Клиент создаёт бота в `@BotFather` (`/newbot`), получает токен.
2. Присылает тебе `user_id:token` (свой Telegram ID и токен).
3. Ты дописываешь строку `CLIENT_<user_id>=<token>` в `/workspace/.env`.
4. Перезапускаешь диспетчер — бот «оживает».

Смена токена: клиент присылает новый `user_id:token`, ты заменяешь строку,
перезапускаешь. Старый бот перестаёт работать.

## 4. Запуск

```bash
cd /workspace
python3 -m pro_code.main
```

Что происходит при старте:

1. Читается `.env`, собирается список ботов.
2. Каждый токен проверяется через `getMe`. Неактивный → клиент помечается
   `inactive`, бот не поднимается, в лог пишется предупреждение. Остальные работают.
3. На каждого активного бота — свой поток polling (`getUpdates`).
4. Отдельный поток — serve-цикл автопостинга (только для плана `auto`).

## 5. Где лежат данные

| Файл | Что |
|---|---|
| `/workspace/data/clients.db` | клиенты, привязки, каналы, LLM-расход |
| `/workspace/data/ruch.db` | ручной модуль: посты, повторы, статистика, бэкапы, черновики диалога |
| `/workspace/data/rezerv.db` | отложенные посты ручного модуля |
| `/workspace/data/kino_auto.db` | автопостинг: слоты, резерв, прогоны |
| `/workspace/data/user_backups/` | пользовательские бэкапы (CSV/TXT, 2 последних) |
| `/workspace/_work/backups/` | главный бэкап (только мастер, 2 последних) |

Все `data/*.db*` закрыты `.gitignore`.

## 6. Проверка перед боевым запуском

```bash
# 1. Конфиг читается, токены на месте
python3 -c "from pro_code.config import load_config; c = load_config(); print(c.admin_chat_id, len(c.clients))"

# 2. Тесты модуля ручного постинга
python3 -m pytest project_ruch/tests -q

# 3. Синтаксис всех модулей
python3 -m compileall -q pro_code project_ruch rrA info rezerv_post
```

## 7. Что проверить в живом Telegram

Сеть в песочнице закрыта, поэтому эти шаги — на твоей машине:

1. `/start` боту клиента → главное меню.
2. Переслать пост из канала → бот запоминает канал.
3. `/post` → выбрать канал кнопкой → отправить текст → `/send` → пост в канале.
4. Фото с подписью → `/send` → фото в канале, `media_path` стал `file_id`.
5. `/del_last` → пост удалён из канала.
6. `/reserve` → пост в `rezerv.db`.

## 8. Чего в боевом конфиге быть не должно

- Токенов в коде, в git, в логах. `pro_code/tg_api.py` чистит токен из текста
  ошибок (`token[:12]…`), но не логируй `.env` целиком.
- `ADMIN_CHAT_ID` клиента. Мастер — один.
- Паролей и ключей LLM в `CLIENT_*` — они живут в едином `/workspace/.env`.