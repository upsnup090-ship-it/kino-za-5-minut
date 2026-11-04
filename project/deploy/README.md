# Деплой Kino serve + admin (автопостинг канала владельца)

> **Полная инструкция «с нуля»:** `docs/industrial/RUN.md`.
> **Справочник переменных `.env`:** `docs/industrial/deploy.md`.
> **Диспетчер ботов клиентов:** `deploy/README.md`.

Два systemd-сервиса:

| юнит | что делает | тип |
|---|---|---|
| `kino.service` | `kino serve` — публикации по расписанию | `Type=notify` + `WatchdogSec=180` |
| `kino-admin.service` | `kino admin` — админка расписания в личке бота | `Type=simple` |

Разделены сознательно: падение админки не должно тянуть публикации, и
наоборот. БД одна (`data/kino.db`), SQLite держит несколько соединений, а
`serve` не держит транзакций между тиками.

## Требования

- VPS с systemd (Debian/Ubuntu).
- Проект скопирован в `/opt/kino/project`.
- Единый `.env` заполнен реальными значениями (шаблон: `/workspace/.env.example`).
- Виртуальное окружение создано: `venv/`.

## Шаг 1. Сетка слотов в БД

Режим `slots` читает расписание из `data/kino.db`. Заполнить его нужно один
раз, до первого старта в этом режиме:

```bash
cd /opt/kino/project
sudo -u kino venv/bin/python -m kino schedule seed
sudo -u kino venv/bin/python -m kino schedule list
```

Повторный `seed` ничего не дублирует и не воскрешает удалённые слоты.

## Шаг 2. Установка юнитов

```bash
sudo cp deploy/kino.service deploy/kino-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kino kino-admin
```

## Шаг 3. Проверка

```bash
sudo systemctl status kino kino-admin     # оба active (running)
journalctl -u kino -n 100                 # последние 100 строк лога
journalctl -u kino-admin -n 50
systemctl show kino -p WatchdogUSec       # WatchdogUSec=3min
```

В `systemctl status kino` строка `Status:` показывает, что процесс сам
сообщил: `режим slots, слотов 13` или `режим windows, окон 2`.

## Как это работает

- **`Type=notify` + `WatchdogSec=180`**: `serve` шлёт `READY=1` на старте и
  `WATCHDOG=1` в начале каждого тика (`tick_seconds=60`). Не пришёл пинг за
  180с — systemd перезапускает процесс. Это лечит **зависание**, которое
  `Restart=always` не видит: процесс жив, но застрял в сетевом вызове.
- **`Restart=always` + `RestartSec=30`**: процесс упал — systemd поднимет его
  через 30с. Расписание и счётчик дня подхватятся из `data/state.json` и
  `data/kino.db` — дублей не будет.
- **`KillSignal=SIGTERM` + `TimeoutStopSec=300`**: serve доигрывает текущий
  пост перед выходом. На SIGTERM отправляется `STOPPING=1`, чтобы systemd не
  считал долгую остановку зависанием watchdog.
- **Логи**: `StandardOutput=journal` (смотри `journalctl -u kino`) + serve сам
  пишет в `logs/kino.log` с ротацией и автоочисткой по возрасту/размеру.
- **Права**: пользователь `kino` должен писать в `data/` и `logs/`.

## Обновление кода на сервере

```bash
cd /opt/kino/project && sudo -u kino git pull origin main
sudo -u kino /opt/kino/app/deploy/update.sh --restart
```

или одной командой:

```bash
cd /opt/kino/project && make update
```

## Управление

```bash
sudo systemctl status kino kino-admin   # статус
sudo systemctl restart kino             # перезапуск публикаций
sudo systemctl stop kino kino-admin     # остановка (SIGTERM, пост доигрывается)
journalctl -u kino -f                   # логи в реальном времени
```

## Диагностика

| Проблема | Что проверить |
|----------|---------------|
| Сервис не стартует | `journalctl -u kino -n 50` — ошибка конфига/окружения |
| `start operation timed out. Terminating.` | процесс не прислал `READY=1` за `TimeoutStartSec` (по умолчанию 90с) |
| Крутится и падает | `Restart=always` поднимет; смотри `journalctl` на причину |
| Рестарты каждые ~3 минуты | watchdog: тик не доходит до пинга. `grep WATCHDOG journalctl -u kino` |
| Нет постов по расписанию | `logs/kino.log` — «ждём: следующий слот …» |
| «в БД нет включённых слотов» | `kino schedule seed` (шаг 1) |
| Неверные ключи | `load_config` упадёт на старте с `ConfigError` — в journald |

## Откат

```bash
sudo systemctl disable --now kino kino-admin
sudo rm /etc/systemd/system/kino.service /etc/systemd/system/kino-admin.service
sudo systemctl daemon-reload
```

Откат режима расписания без удаления юнита — одной строкой в
`config/settings.yaml`: `schedule.mode: windows`.