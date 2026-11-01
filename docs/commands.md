# Команды проекта «Кино за 5 минут»

Один файл со всеми командами: локально, на сервере, диагностика, откат.
Пути — боевые (`/opt/kino/project`). Если проект лежит иначе, поменяй префикс.

---

## 0. Обозначения

```bash
# Локально (в репозитории):
cd /workspace/project
venv/bin/python -m kino ...          # CLI

# На сервере:
cd /opt/kino/project
sudo -u kino venv/bin/python -m kino ...   # от пользователя kino
```

`sudo -u kino` нужен всегда, когда команда пишет в `data/` или `logs/`:
иначе файлы станут root-овыми, и сервис потеряет к ним доступ.

---

## 1. Разработка: тесты и проверки

```bash
cd /workspace/project

# Полный прогон тестов (боевой набор)
venv/bin/python -m pytest tests/ -q

# Полный прогон без сетевых тестов (быстрее, notify может висеть)
venv/bin/python -m pytest tests/ -q --ignore=tests/test_notify.py

# Один файл / один тест
venv/bin/python -m pytest tests/test_choose_routing.py -q
venv/bin/python -m pytest tests/test_tg_conflict.py::test_run_reports_conflict_once -v

# Типы
venv/bin/python -m mypy kino/

# Если тест виснет — найти, какой именно, с таймаутом:
timeout 60 venv/bin/python -m pytest tests/<файл> -v
```

Ожидаемая база: **1014 passed**, `mypy: 40 файлов, 0 ошибок`.

---

## 2. Локальный прогон

```bash
cd /workspace/project

# Один пост без публикации (по умолчанию --dry-run)
venv/bin/python -m kino run --dry-run

# Реальная публикация в канал
venv/bin/python -m kino run --publish

# Черновик по заданной теме, без публикации
venv/bin/python -m kino draft "Дюна 3 получила дату премьеры" --format news

# Только тело запроса к LLM, без вызова модели
venv/bin/python -m kino draft "тема" --format facts --dry-run

# Один проход цикла serve и выход (отладка расписания)
venv/bin/python -m kino serve --once

# Цикл с ускоренным тиком (не ждать 60с между проверками)
venv/bin/python -m kino serve --once --tick-seconds 5
```

---

## 3. Расписание (слоты)

```bash
cd /workspace/project

# Заполнить сетку владельца (13 слотов). Повторный seed не дублирует.
venv/bin/python -m kino schedule seed

# Показать слоты по дням
venv/bin/python -m kino schedule list

# Добавить слот: день (0-6 или пн..вс) + время HH:MM
venv/bin/python -m kino schedule add пн 11:05

# Удалить слот
venv/bin/python -m kino schedule del пн 11:05

# Включить / выключить слот
venv/bin/python -m kino schedule on пн 11:05
venv/bin/python -m kino schedule off пн 11:05

# Убрать все слоты дня
venv/bin/python -m kino schedule clear вс

# Последние записи журнала прогонов
venv/bin/python -m kino schedule runs --limit 20
```

---

## 4. Админка в Telegram

```bash
cd /workspace/project

# Запустить админку (опрос обновлений, 24/7)
venv/bin/python -m kino admin

# Один опрос и выход (отладка)
venv/bin/python -m kino admin --once

# Напечатать экран в консоль, не трогая Telegram
venv/bin/python -m kino admin --screen schedule
venv/bin/python -m kino admin --screen preview
venv/bin/python -m kino admin --screen runs
```

Команды в самом боте: `/start`, `/menu`, `/slot ...`.

---

## 5. Маршрутизация форматов

```bash
cd /workspace/project

# Оффлайн-замер по кэшу фидов (без LLM и без сети)
venv/bin/python scripts/routing_report.py

# Замер с JSON-выводом для машинной обработки
venv/bin/python scripts/routing_report.py --json /tmp/routing.json

# Замер по другому кэшу
venv/bin/python scripts/routing_report.py --cache-dir /tmp/cache

# Демо-прогон форматов (проверка промптов без публикации)
venv/bin/python scripts/demo_posts.py
venv/bin/python scripts/demo_posts.py news facts quote
```

### Что смотреть в замере

Красные флаги шлюза §10 (design-док):

| флаг | порог | смысл |
|---|---|---|
| facts допущен | < 10% | фактурные форматы задыхаются |
| news (фолбэк) | > 40% | канал сужается до новостей |
| формат с 0 допусков | за неделю | мёртвый формат |

Замер по кэшу 18.10 дал **news 55.3%** — флаг горит. Кэш без 11 YT-фидов
занижает `video`/`trailer`, поэтому решение по весам (Фаза 6) принимается
только после **серверного** замера со свежими фидами.

---

## 6. Сервер: развёртывание с нуля

```bash
# Шаг 1. Пользователь
sudo useradd -r -m -d /opt/kino kino
sudo chown -R kino:kino /opt/kino/project

# Шаг 2. Виртуальное окружение (если ещё нет)
cd /opt/kino/project
sudo -u kino python3 -m venv venv
sudo -u kino venv/bin/pip install -r requirements.txt

# Шаг 3. .env (шаблон — docs/env.example)
sudo -u kino cp docs/env.example .env
sudo -u kino nano .env          # заполнить реальными значениями
sudo chmod 600 .env

# Шаг 4. Сетка слотов в БД (один раз, до первого старта)
sudo -u kino venv/bin/python -m kino schedule seed
sudo -u kino venv/bin/python -m kino schedule list

# Шаг 5. Юниты systemd
sudo cp deploy/kino.service deploy/kino-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kino kino-admin

# Шаг 6. Проверка
sudo systemctl status kino kino-admin
journalctl -u kino -n 100
systemctl show kino -p WatchdogUSec
```

---

## 7. Сервер: обновление кода

```bash
cd /opt/kino/project

# 1. Забрать код
sudo -u kino git pull origin main

# 2. Если менялись зависимости
sudo -u kino venv/bin/pip install -r requirements.txt

# 3. Перезапустить сервисы
sudo systemctl restart kino kino-admin

# 4. Убедиться, что поднялись
sudo systemctl status kino kino-admin
journalctl -u kino -n 30
```

**Правило:** после коммита — сразу `git push origin main`. Сервер работает
только на том, что в origin; 19 непушенных коммитов 18.10 стоили дня
работы на старом коде.

---

## 8. Сервер: замер перед Фазой 6

```bash
cd /opt/kino/project

# 1. Свежие фиды: один прогон сбора (без публикации) наполняет кэш
sudo -u kino venv/bin/python -m kino run --dry-run

# 2. Сколько файлов в кэше и на какую дату
ls -la data/feed_cache/*.xml | wc -l
ls -lat data/feed_cache/*.xml | head -5

# 3. Замер маршрутизации
sudo -u kino venv/bin/python scripts/routing_report.py

# 4. То же с JSON — приложить к решению
sudo -u kino venv/bin/python scripts/routing_report.py --json /tmp/routing-server.json
```

Смотрим на три цифры: доля `news` в ожидаемом выборе, допуск `facts`,
форматы с нулём допусков. По ним выбирается сценарий S1 (мягкий) / S2
(сильный) из §9.2 design-дока, или «не трогать».

**Веса не меняем без команды владельца** — они влияют на живой канал сразу.

---

## 9. Сервер: canary-наблюдение

```bash
cd /opt/kino/project

# Живой лог публикаций
journalctl -u kino -f

# Лог самого приложения (с ротацией)
tail -f logs/kino.log

# Только строки маршрутизации (Фаза 4)
journalctl -u kino -n 500 | grep "маршрут:"

# Статистика форматов (Фаза 5): 6 колонок, kinds и allowed_count
tail -20 logs/format_stats.csv

# Доля фолбэка news за всё время
awk -F, 'NR>1 {c[$2]++} END {for (f in c) print c[f], f}' logs/format_stats.csv | sort -rn

# Доля news за последние 100 строк
tail -100 logs/format_stats.csv | awk -F, 'NR>1 && $2=="news"' | wc -l
```

---

## 10. Диагностика

```bash
# Статус обоих сервисов
sudo systemctl status kino kino-admin

# Последние строки логов
journalctl -u kino -n 100 --no-pager
journalctl -u kino-admin -n 50 --no-pager

# Ошибки за сутки
journalctl -u kino --since "24 hours ago" -p err --no-pager

# Watchdog-рестарты (частые = зависание в сетевом вызове)
journalctl -u kino --since "24 hours ago" | grep -c WATCHDOG

# Кто держит 409 Conflict (два экземпляра админки)
ps aux | grep "kino admin"
journalctl -u kino-admin -n 50 | grep 409

# Проверить, что токен не утёк в лог (задача #70)
grep -c "bot[0-9]*:" logs/kino.log

# Показать экран админки без Telegram
sudo -u kino venv/bin/python -m kino admin --screen schedule

# Журнал прогонов из БД
sudo -u kino venv/bin/python -m kino schedule runs --limit 30
```

| Симптом | Что проверить |
|---|---|
| Сервис не стартует | `journalctl -u kino -n 50` — ошибка конфига/окружения |
| `start operation timed out` | не пришёл `READY=1` за `TimeoutStartSec` (90с) |
| Рестарты каждые ~3 мин | watchdog: тик не доходит до пинга |
| Нет постов по расписанию | `logs/kino.log` — «ждём: следующий слот …» |
| «в БД нет включённых слотов» | `kino schedule seed` |
| Админка молчит | `journalctl -u kino-admin`; 409 = второй экземпляр |
| LLM флапает | ретраи 2/4/8с; стабильно — смотреть `LLM_PROXY_URL` |

---

## 11. Откат

```bash
# Откат маршрутизации — одна строка в config/formats.yaml:
#   routing:
#     enabled: false
sudo -u kino sed -i 's/^\(\s*\)enabled: true/\1enabled: false/' config/formats.yaml
sudo systemctl restart kino

# Откат режима расписания на окна — config/settings.yaml:
#   schedule.mode: windows
sudo systemctl restart kino

# Откат кода на предыдущий коммит
cd /opt/kino/project
sudo -u kino git log --oneline -5
sudo -u kino git checkout <sha>
sudo systemctl restart kino kino-admin

# Полное удаление сервисов
sudo systemctl disable --now kino kino-admin
sudo rm /etc/systemd/system/kino.service /etc/systemd/system/kino-admin.service
sudo systemctl daemon-reload
```

---

## 12. Git: ежедневный цикл

```bash
cd /workspace/project

git status --short                 # что изменено
git add -A                         # или точечно: git add kino/choose.py
git commit -m "тип(область): что сделано"
git push origin main               # ВСЕГДА сразу после коммита

git log --oneline -10              # последние коммиты
git log origin/main..HEAD --oneline   # что ещё не запушено (должно быть пусто)
```

Типы коммитов: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`.

---

## 13. Фазы маршрутизации: статус

| Фаза | Статус | Коммит |
|---|---|---|
| 0. Дизайн-док + решения владельца | ✅ | — |
| 1. `kino/routing.py` + тесты | ✅ | (19.10) |
| 2. Конфиг `kind`/`routing` + валидация | ✅ | dd92b34 |
| 3. `routing_report.py` + замер | ✅ | f764c07 |
| 4. Подключение в `choose.py` | ✅ | 91d238f |
| 5. CSV-статистика + dry-run вывод | ✅ | 2ed2239 |
| 6. Калибровка весов | ⏸ ждёт серверного замера | — |

Фаза 6 — не работа, а решение: сначала замер (§8 выше), потом выбор
сценария S1/S2/ничего, потом правка `config/formats.yaml`.