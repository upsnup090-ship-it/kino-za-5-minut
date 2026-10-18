# Развёртывание 24/7 на VPS

Ставит `kino serve` как systemd-сервис: автозапуск, `Restart=always` при
падении, мягкая остановка по SIGTERM.

## Требования

- VPS с systemd (Debian/Ubuntu).
- Проект скопирован в `/opt/kino/project`.
- `.env` заполнен реальными значениями (шаблон: `docs/env.example`).
- Виртуальное окружение создано: `venv/`.

## Шаг 1. Пользователь

Запускаем от обычного пользователя, не root:

```bash
sudo useradd -r -m -d /opt/kino kino
sudo chown -R kino:kino /opt/kino/project
```

Если проект не в `/opt/kino/project` — поменяй `WorkingDirectory` и `ExecStart`
в `deploy/kino.service`.

## Шаг 2. Установка юнита

```bash
sudo cp deploy/kino.service /etc/systemd/system/kino.service
sudo systemctl daemon-reload
sudo systemctl enable --now kino
```

## Шаг 3. Проверка

```bash
sudo systemctl status kino      # должен быть active (running)
journalctl -u kino -n 100       # последние 100 строк лога
```

## Управление

```bash
sudo systemctl status kino       # статус
sudo systemctl restart kino      # перезапуск
sudo systemctl stop kino         # остановка (SIGTERM, пост доигрывается)
journalctl -u kino -f            # логи в реальном времени
```

## Как это работает

- **`Restart=always` + `RestartSec=30`**: процесс упал — systemd поднимет его
  через 30с. Расписание и счётчик дня подхватятся из `data/state.json` —
  дублей не будет.
- **`KillSignal=SIGTERM` + `TimeoutStopSec=120`**: serve доигрывает текущий
  пост перед выходом, публикация не обрывается на середине.
- **Логи**: `StandardOutput=journal` (смотри `journalctl -u kino`) + serve сам
  пишет в `logs/kino.log` с ротацией и автоочисткой по возрасту/размеру.
- **Права**: пользователь `kino` должен писать в `data/` и `logs/`.

## Диагностика

| Проблема | Что проверить |
|----------|---------------|
| Сервис не стартует | `journalctl -u kino -n 50` — ошибка конфига/окружения |
| Крутится и падает | `Restart=always` поднимет; смотри `journalctl` на причину |
| Нет постов по расписанию | `logs/kino.log` — «пора: ... следующий слот» |
| Неверные ключи | `load_config` упадёт на старте с `ConfigError` — в journald |

## Откат

```bash
sudo systemctl disable --now kino
sudo rm /etc/systemd/system/kino.service
sudo systemctl daemon-reload
```