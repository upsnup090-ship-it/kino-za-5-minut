# Запуск диспетчера на сервере — пошагово

*Краткая выжимка. Подробности — `deploy/README.md` (он в git, едет с кодом).*

## 0. Что где лежит

```
/opt/kino/project/        git-копия: pro_code/ project/ project_ruch/ rrA/ deploy/ scripts/
/opt/kino/project/.env    единый боевой конфиг (chmod 600, вне git)
/opt/kino/project/data/   БД (clients.db, ruch.db, kino_auto.db, rezerv.db) — вне git
```

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

Обязательные ключи: `ADMIN_CHAT_ID`, `MASTER_BOT_TOKEN`, `TOKEN_ENCRYPTION_KEY`.

## 4. Данные (если переезд)

```bash
rsync -av data/ /opt/kino/project/data/
```

## 5. Проверка до старта

```bash
sudo -u kino venv/bin/python scripts/smoke.py
```

Все строки `[PASS]` → можно стартовать. Хотя бы один `[FAIL]` → смотри
пояснение в той же строке, чинить до запуска.

## 6. Юнит и старт

```bash
cd /opt/kino/project
sudo cp deploy/kino-dispatcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kino-dispatcher
```

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
