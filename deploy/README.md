# Деплой диспетчера (Киномодули / Ruch)

Диспетчер (`pro_code`) — один процесс: polling ботов клиентов и главного бота,
плюс serve-цикл автопостинга в отдельном потоке.

| юнит | что делает |
|---|---|
| `deploy/kino-dispatcher.service` | `python -m pro_code.main` — боты клиентов + автопостинг |
| `project/deploy/kino.service` | `kino serve` — канал **мастера** (не клиентов) |

## Пути

```
/opt/kino/project        git-копия (pro_code/, project/, project_ruch/, rrA/)
/opt/kino/project/.env   единый боевой конфиг (chmod 600, вне git)
/opt/kino/project/data/  БД: clients.db, ruch.db, kino_auto.db, rezerv.db
```

## Шаг 1. Пользователь и код

```bash
sudo useradd -r -m -d /opt/kino kino
sudo chown -R kino:kino /opt/kino/project
```

## Шаг 2. Окружение и зависимости

```bash
cd /opt/kino/project
python3 -m venv venv
sudo -u kino venv/bin/pip install -r pro_code/requirements.txt
# Kino-зависимости (автопостинг) — из своего списка, в тот же venv:
sudo -u kino venv/bin/pip install -r project/requirements.txt
```

Если `venv` случайно создан от root — `sudo chown -R kino:kino venv` перед pip.

## Шаг 3. Конфиг

```bash
cd /opt/kino/project
# либо скопировать готовый файл, либо:
sudo -u kino nano .env      # ADMIN_CHAT_ID, MASTER_BOT_TOKEN, TOKEN_ENCRYPTION_KEY
sudo chmod 600 .env
```

`TOKEN_ENCRYPTION_KEY` **не менять после того, как клиенты завели токены** —
иначе они вставляют свои токены заново.

## Шаг 4. Перенос данных (если переезжаем)

`data/*.db` вне git — переносятся отдельно:

```bash
rsync -av data/ /opt/kino/project/data/
```

## Шаг 5. Проверка до старта

```bash
sudo -u kino venv/bin/python scripts/smoke.py
```

Проверяет `.env`, конфиг, `getMe` каждого токена, `import kino`, открытие всех
БД. Ничего не публикует.

## Шаг 6. Установка юнита

```bash
cd /opt/kino/project
sudo cp deploy/kino-dispatcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kino-dispatcher
```

## Шаг 7. Проверка

```bash
systemctl status kino-dispatcher
journalctl -u kino-dispatcher -n 100 --no-pager
```

В логе ожидается: `конфиг загружен: admin=…, клиентов=N`, затем
`polling запущен для user_id=…` на каждого клиента, `serve-цикл запущен`.

## Обновление кода

```bash
cd /opt/kino/project && sudo -u kino git pull origin main
sudo systemctl restart kino-dispatcher
```

## Управление

```bash
sudo systemctl restart kino-dispatcher
sudo systemctl stop kino-dispatcher
journalctl -u kino-dispatcher -f
```

## Диагностика

| Симптом | Причина |
|---|---|
| `ConfigError: ADMIN_CHAT_ID не задан` | пусто в `.env` |
| `MASTER_BOT_TOKEN неактивен` | токен битый/отозван в @BotFather |
| `бот user_id=… пропущен (неактивный токен)` | клиент пересоздал бота — нужен новый токен |
| бот молчит | `409 Conflict` в логе — один токен поллят два процесса |
| `Kino недоступен` | не установлен `project/requirements.txt` |
| `SQLite objects created in a thread` | на сервере старый код (до аудита) — нужен `git pull` |
