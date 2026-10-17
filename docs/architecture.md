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
| Статья | `article.py` | заголовок + текст | URL страницы graph.org (Instant View) |
| Публикация | `publish.py` | `caption`, `image_url` | фото → текст → ЛС админу при ошибке |

## Статьи (вариация `article`)

Статья создаётся на **graph.org** (`api.graph.org`) — том же протоколе, что у
telegra.ph (`createAccount` / `createPage`, узлы Node в JSON). Страница
открывается в Telegram как Instant View.

- Токен: `GRAPH_TOKEN` из окружения, иначе `createAccount` и сохранение в
  `data/graph_token.txt` (атомарно).
- Прокси: `GRAPH_PROXY_URL`, иначе `LLM_PROXY_URL`, иначе `PROXY_URL`.
  `api.graph.org`, как и `api.telegra.ph`, напрямую из этой сети не отвечает
  (ConnectTimeout), через прокси работает штатно.
- Сетевые сбои повторяются (3 попытки), HTTP-ошибки API — нет.
- Недоступность статьи не роняет пост: `article` → фолбэк на фото.

## Источники новостей

Пайплайн поддерживает два источника, переключаются через `SOURCE_MODE`
(или `horizon.source` в `settings.yaml`):

- **`rss`** (по умолчанию) — `collect.py` тянет RSS/Atom из `config/sources.yaml`
  через `feedparser`, параллельно, с фильтром свежести.
- **`horizon`** — `horizon.py` запускает `uv run horizon --hours N`, читает
  свежий `.md` из `data/summaries/` и разбирает его в тот же список `FeedItem`.

Состав `sources.yaml`: Deadline, Variety, THR, SlashFilm, IndieWire, Guardian,
RollingStone, AVClub, Reddit, а также азиатские и российские издания —
THR/Asia, SCMP/Culture, JapanTimes/Culture, CNA/Entertainment,
Yonhap/Entertainment, InterMedia, Film.ru. YouTube-фиды убраны: они отдавали
SSLError/ReadTimeout, а RuTube закрыл RSS (404 на всех путях).

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

## Картинки

`image.py` ищет картинку в Wikipedia по двум эндпоинтам:

- `w/rest.php/v1/search/page` — поиск статьи по названию (limit 3);
- `api/rest_v1/page/summary/<key>` — превью по точному ключу. Это основной
  путь: у свежих фильмов поиск превью не отдаёт, хотя статья и картинка есть.

Отбор: первый результат поиска берётся как основной (Wikipedia ранжирует
сама), отбор по слову «фильм» применяется только если первая страница не
пересекается с запросом. Между запросами пауза 0.8с, 429 — повтор с
уважением к `Retry-After`. Если картинки нет — пост уходит без неё, чужая
картинка не подставляется.

## Безопасность

- Секреты только из `.env` (не коммитится).
- Логи без токенов, ключей и chat_id.
- Сеть: `api.telegram.org`, `LLM_BASE_URL`, `api.graph.org` (статьи), Wikipedia (картинки), RSS-источники из `config/sources.yaml`.