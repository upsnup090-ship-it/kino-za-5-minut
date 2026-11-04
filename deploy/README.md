# Деплой диспетчера (pro_code — боты клиентов + serve-цикл)

> **Полная инструкция «с нуля»:** `docs/industrial/RUN.md`.
> **Справочник переменных `.env`:** `docs/industrial/deploy.md`.

Юнит: `deploy/kino-dispatcher.service` → `python -m pro_code.main`.
(Автопостинг Kino для канала владельца — `project/deploy/kino.service`, описан в `project/deploy/README.md`.)

> **Канонический источник:** `deploy/kino-dispatcher.service`.
> Копия в `project/deploy/kino-dispatcher.service` синхронизируется с ним
> автоматически (оба файла идентичны). Правь только `deploy/kino-dispatcher.service`.

## Пути

```
/opt/kino/project        git-копия (pro_code/, project/, project_ruch/, rrA/)
/opt/kino/project/.env   единый боевой конфиг (chmod 600, вне git)
/opt/kino/project/data/  БД: clients.db, ruch.db, kino_auto.db, rezerv.db
```

## Быстрый старт (если всё уже настроено)

```bash
cd /opt/kino/project && sudo -u kino git pull origin main
sudo systemctl restart kino-dispatcher
```

## Установка юнита

```bash
sudo cp deploy/kino-dispatcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kino-dispatcher
```

## Проверка

```bash
systemctl status kino-dispatcher
journalctl -u kino-dispatcher -n 100 --no-pager
```

Ожидаемые строки в логе: `конфиг загружен: admin=…, клиентов=N`,
`polling запущен для user_id=…`, `serve-цикл запущен`.

## Диагностика

| Симптом | Причина |
|---|---|
| `ConfigError: ADMIN_CHAT_ID не задан` | пусто в `.env` |
| `MASTER_BOT_TOKEN неактивен` | токен битый/отозван в @BotFather |
| `бот user_id=… пропущен (неактивный токен)` | клиент пересоздал бота — нужен новый токен |
| бот молчит | `409 Conflict` в логе — один токен поллят два процесса |
| `Kino недоступен` | не установлен `project/requirements.txt` |
| `SQLite objects created in a thread` | на сервере старый код (до аудита) — `git pull` |

## Управление

```bash
sudo systemctl restart kino-dispatcher
sudo systemctl stop kino-dispatcher
journalctl -u kino-dispatcher -f
```