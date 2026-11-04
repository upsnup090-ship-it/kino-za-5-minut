# Запуск диспетчера на сервере — пошагово

*Краткая выжимка. Подробности — `deploy/README.md` (он в git, едет с кодом).*

## 0. Что где лежит

```
/opt/kino/project/            git-копия: pro_code/ project/ project_ruch/ rrA/ deploy/ scripts/
/opt/kino/project/.env        единый боевой конфиг (chmod 600, вне git)
/opt/kino/project/data/       БД: clients.db, ruch.db, rezerv.db, kino_auto.db (диспетчер)
/opt/kino/project/project/data/kino.db   БД автопостинга Kino (слоты, резерв, прогоны)
```

Пути — от корня `/opt/kino/project` (внутри ЗИПа папка называется `project/`,
поэтому БД Kino — во вложенном `project/data/`).

## 1. Обновить код

```bash
cd /opt/kino/project
sudo -u kino git pull origin main
```

## 2. Окружение (один раз)

```bash
cd /opt/kino/project
python3 -m venv venv
sudo chown -R kino:kino venv                      # иначе pip от kino не пишет
sudo -u kino venv/bin/pip install -r pro_code/requirements.txt
sudo -u kino venv/bin/pip install -r project/requirements.txt
```

## 3. Конфиг (один раз)

```bash
cd /opt/kino/project
sudo -u kino nano .env
sudo chmod 600 .env
```

Обязательные ключи: `ADMIN_CHAT_ID`, `MASTER_BOT_TOKEN`, `TOKEN_ENCRYPTION_KEY`
(последний — обязателен при использовании модели A: клиент активирует бота
через `/start`, токен хранится в `clients.db` зашифрованным).
Для автопостинга (клиенты с планом `auto`): `BOT_TOKEN`, `CHANNEL_CHAT_ID`,
`LLM_PROVIDER=openrouter`, `LLM_BASE_URL=https://ai.reqline.ru/v1`,
`OPENROUTER_API_KEY`. Прокси: `PROXY_URL`/`GRAPH_PROXY_URL` — `api.graph.org`
напрямую из сети сервера не отвечает. Полный шаблон — `/workspace/.env.example`.

## 4. Данные (если переезд)

```bash
# источник указываем явно — например, перенос со старого хоста
rsync -av /path/to/old/data/ /opt/kino/project/data/
# БД Kino — отдельно, она во вложенном project/data/
rsync -av /path/to/old/project/data/ /opt/kino/project/project/data/
```

## 5. Проверка до старта

```bash
sudo -u kino venv/bin/python scripts/smoke.py
```

Все строки `[PASS]` → можно стартовать. Хотя бы один `[FAIL]` → смотри
пояснение в той же строке, чинить до запуска.

## 6. Юниты (3 штуки) и старт

```bash
cd /opt/kino/project
sudo cp deploy/kino-dispatcher.service deploy/kino.service deploy/kino-admin.service \
     /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kino-dispatcher kino kino-admin
```

| юнит | что делает |
|---|---|
| `kino-dispatcher` | диспетчер ботов (`pro_code.main`): polling ботов клиентов, serve-цикл автопостинга |
| `kino` | автопостинг Kino (`kino serve`) — канал владельца |
| `kino-admin` | админ-бот расписания (`kino admin`) |

## 6.1. Сетка слотов (один раз)

Автопостинг Kino публикует по расписанию из БД — без слотов не выйдет
ничего:

```bash
cd /opt/kino/project/project
sudo -u kino ../venv/bin/python -m kino schedule seed
```

Правка слотов потом — через админ-бота (`kino-admin`, инлайн-календарь
в личке) или `/slot add` / `/slot del`.

## 7. Что смотреть

```bash
systemctl status kino-dispatcher
journalctl -u kino-dispatcher -n 100 --no-pager
```

Ожидаемые строки в логе:

- `конфиг загружен: admin=…, клиентов=N`
- `polling запущен для user_id=…` — по строке на каждого клиента
- `serve-цикл запущен (интервал 60с)`
- `главный бот запущен` — если задан `MASTER_BOT_TOKEN`

## 8. Обновление кода в дальнейшем

```bash
cd /opt/kino/project && sudo -u kino git pull origin main
sudo systemctl restart kino-dispatcher
```

## Частые ошибки

| Симптом | Причина |
|---|---|
| `ConfigError: ADMIN_CHAT_ID не задан` | пусто в `.env` |
| `Permission denied` при pip | `venv` создан от root → `sudo chown -R kino:kino venv` |
| `бот молчит`, в логе `409 Conflict` | один токен поллят два процесса (не запущены два экземпляра?) |
| `SQLite objects created in a thread` | на сервере старый код (до аудита) → `git pull` |
| `Kino недоступен` | не поставлен `project/requirements.txt` |
