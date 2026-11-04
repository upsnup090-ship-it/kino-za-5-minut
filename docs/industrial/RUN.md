# Запуск на сервере — пошаговая инструкция

*Сводный документ: от разворачивания с нуля до обновления кода.*
*Справочник переменных `.env` — `docs/industrial/deploy.md`.*
*Детали юнитов — `deploy/README.md` (диспетчер) и `project/deploy/README.md` (Kino).*

---

## 0. Что где лежит

```
/opt/kino/project/            git-копия: pro_code/ project/ project_ruch/ rrA/ deploy/ scripts/
/opt/kino/project/.env        единый боевой конфиг (chmod 600, вне git)
/opt/kino/project/data/       БД: clients.db, ruch.db, rezerv.db, kino_auto.db (диспетчер)
/opt/kino/project/project_auto/data/kino.db   БД автопостинга Kino (слоты, резерв, прогоны)
```

Пути — от корня `/opt/kino/project` (внутри ЗИПа папка называется `project/`,
поэтому БД Kino — во вложенном `project/data/`).

## 1. Пользователь и код

```bash
sudo useradd -r -m -d /opt/kino kino
# скопировать код в /opt/kino/project (git clone / распаковать ЗИП)
sudo chown -R kino:kino /opt/kino/project
```

## 2. Окружение

```bash
cd /opt/kino/project
python3 -m venv venv
sudo chown -R kino:kino venv                      # иначе pip от kino не пишет
sudo -u kino venv/bin/pip install -r pro_code/requirements.txt
sudo -u kino venv/bin/pip install -r project/requirements.txt
```

## 3. Конфиг

```bash
cd /opt/kino/project
sudo -u kino nano .env
sudo chmod 600 .env
```

Обязательные ключи: `ADMIN_CHAT_ID`, `MASTER_BOT_TOKEN`, `TOKEN_ENCRYPTION_KEY`
(последний обязателен при модели A — клиент активирует бота через `/start`).

Для автопостинга клиентов (план `auto`): `BOT_TOKEN`, `CHANNEL_CHAT_ID`,
`LLM_PROVIDER=openrouter`, `LLM_BASE_URL=https://ai.reqline.ru/v1`,
`OPENROUTER_API_KEY`.

Полный список — см. `docs/industrial/deploy.md` и шаблон `.env.example`.

`TOKEN_ENCRYPTION_KEY` **не менять** после того, как клиенты завели токены —
иначе они вставляют токены заново.

## 4. Данные (при переезде)

```bash
rsync -av /path/to/old/data/ /opt/kino/project/data/
rsync -av /path/to/old/project_auto/data/ /opt/kino/project/project_auto/data/
```

## 5. Проверка до старта

```bash
sudo -u kino venv/bin/python scripts/smoke.py
```

Все `[PASS]` → можно стартовать. Хотя бы один `[FAIL]` → чинить до запуска.

## 6. Сетка слотов Kino (один раз)

Автопостинг Kino публикует по расписанию из БД — без слотов не выйдет ничего:

```bash
cd /opt/kino/project/project_auto
sudo -u kino ../venv/bin/python -m kino schedule seed
```

Правка слотов потом — через админ-бота (`kino-admin`) или `/slot add` / `/slot del`.

## 7. Юниты (3 штуки) и старт

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

> **Канонический источник `kino-dispatcher.service`:** `deploy/kino-dispatcher.service`.
> Копия в `project/deploy/` — синхронизированная; править только
> `deploy/kino-dispatcher.service`, затем копировать в `project/deploy/`.

## 8. Что смотреть

```bash
systemctl status kino-dispatcher kino kino-admin
journalctl -u kino-dispatcher -n 100 --no-pager
```

Ожидаемые строки в логе диспетчера:
- `конфиг загружен: admin=…, клиентов=N`
- `polling запущен для user_id=…` — по строке на каждого клиента
- `serve-цикл запущен (интервал 60с)`
- `главный бот запущен` — если задан `MASTER_BOT_TOKEN`

## 9. Обновление кода

```bash
cd /opt/kino/project && sudo -u kino git pull origin main
sudo systemctl restart kino-dispatcher
```

## Частые ошибки

| Симптом | Причина |
|---|---|
| `ConfigError: ADMIN_CHAT_ID не задан` | пусто в `.env` |
| `Permission denied` при pip | `venv` создан от root → `sudo chown -R kino:kino venv` |
| бот молчит, в логе `409 Conflict` | один токен поллят два процесса |
| `SQLite objects created in a thread` | на сервере старый код → `git pull` |
| `Kino недоступен` | не установлен `project/requirements.txt` |