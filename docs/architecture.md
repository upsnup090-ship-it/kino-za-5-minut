# Архитектура

## Пайплайн

```
collect → choose → draft → judge → image → caption → publish
```

| Шаг | Модуль | Вход | Выход |
|-----|--------|------|-------|
| Сбор | `collect.py` / `horizon.py` | `config/sources.yaml` или дайджест Horizon | `CollectResult(items, broken, total, fresh_count)` |
| Выбор | `choose.py` | `CollectResult.items`, state | `Choice(item, format, quality_score, sad_topic)` |
| Черновик | `draft.py` | `Choice` | черновик (headline, text, question, карточка) |
| Судья | `judge.py` | черновик + источник | финальный текст (откат на черновик при ошибке) |
| Картинка | `image.py` | `wiki_title` | `image_url`, `img_title`, `img_lang` |
| Подпись | `caption.py` | финальный текст + картинка | `caption` (лимит 950/1000) |
| Публикация | `publish.py` | `caption`, `image_url` | фото → текст → ЛС админу при ошибке |

## Источники новостей

Пайплайн поддерживает два источника, переключаются через `SOURCE_MODE`
(или `horizon.source` в `settings.yaml`):

- **`rss`** (по умолчанию) — `collect.py` тянет RSS/Atom из `config/sources.yaml`
  через `feedparser`, параллельно, с фильтром свежести.
- **`horizon`** — `horizon.py` запускает `uv run horizon --hours N`, читает
  свежий `.md` из `data/summaries/` и разбирает его в тот же список `FeedItem`.

Оба источника отдают одинаковый тип `FeedItem(source, title, link, pub_date,
ts, snippet)`, поэтому остальной пайплайн не зависит от выбора источника.

### Horizon

Настройки в `config/settings.yaml` → `horizon`:

| Ключ | Значение по умолчанию | Смысл |
|------|----------------------|-------|
| `source` | `rss` | режим источника (`rss` \| `horizon`) |
| `dir` | `horizon` | путь к клону Horizon (относительно корня проекта) |
| `summaries_dir` | `data/summaries` | куда Horizon кладёт `.md` (относительно `dir`) |
| `run` | `true` | запускать `uv run horizon` перед чтением дайджеста |
| `hours` | `24` | окно новостей для Horizon |
| `timeout_seconds` | `600` | таймаут запуска Horizon |

Переопределить можно переменными окружения: `SOURCE_MODE`, `HORIZON_DIR`,
`HORIZON_SUMMARIES_DIR`.

Парсер `parse_digest()` разбирает Markdown-дайджест: каждая секция уровня
`##`/`###` — одна новость, заголовок — текст секции, ссылка — первая ссылка
в блоке, источник — из строки `Source:`/`来源:` или домен ссылки. Язык
определяется автоматически (EN/ZH). Если `uv` или клон Horizon отсутствуют,
модуль поднимает `HorizonError` с внятным сообщением — пайплайн не падает
молча.

## Состояние

`state.py` хранит `used_links` (до 120) и `used_sigs` (до 60) в `data/state.json`.
Запись атомарная: временный файл + `os.replace`. Lock-файл `data/state.lock`
защищает от параллельных запусков. В dry-run state не сохраняется.

## Режимы

- `python -m kino run` — dry-run: LLM вызывается, публикации и записи state нет.
- `python -m kino run --publish` — боевой запуск.

## Безопасность

- Секреты только из `.env` (не коммитится).
- Логи без токенов, ключей и chat_id.
- Сеть: только `api.telegram.org` и `LLM_BASE_URL`.