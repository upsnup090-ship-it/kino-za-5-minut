# Кино за 5 минут

Telegram-бот автопубликаций о кино. Собирает RSS/Atom, выбирает тему и формат,
пишет черновик через LLM, проверяет вторым вызовом, ищет картинку в Wikipedia
и публикует в канал.

## Установка

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Заполни `.env` реальными значениями. Без этого запуск невозможен.

При первом запуске в `CHANNEL_CHAT_ID` ставь **тестовый** канал. Боевой
подключай только после обкатки.

## Dry-run

По умолчанию ничего не публикуется и state не сохраняется:

```bash
cd /workspace/project
python -m kino run
```

Dry-run всё равно вызывает LLM и тратит токены. Он пропускает только
публикацию и запись state.

## Боевой запуск

```bash
cd /workspace/project
python -m kino run --publish
```

## Cron

Ежедневно в 12:00:

```cron
0 12 * * * cd /workspace/project && venv/bin/python -m kino run --publish >> logs/cron.log 2>&1
```

## Тесты

```bash
pytest
```

## Безопасность

- Токены и ключи только в `.env`, который не коммитится.
- Логи не содержат токенов, ключей и chat_id.
- State сохраняется атомарно: временный файл + rename.
- Lock-файл защищает от параллельных запусков.
- Dry-run не меняет state.