# Архитектура

## Пайплайн

```
collect → choose → draft → judge → image → caption → publish
```

| Шаг | Модуль | Вход | Выход |
|-----|--------|------|-------|
| Сбор | `collect.py` | `config/sources.yaml` | `CollectResult(items, broken, total, fresh_count)` |
| Выбор | `choose.py` | `CollectResult.items`, state | `Choice(item, format, quality_score, sad_topic)` |
| Черновик | `draft.py` | `Choice` | черновик (headline, text, question, карточка) |
| Судья | `judge.py` | черновик + источник | финальный текст (откат на черновик при ошибке) |
| Картинка | `image.py` | `wiki_title` | `image_url`, `img_title`, `img_lang` |
| Подпись | `caption.py` | финальный текст + картинка | `caption` (лимит 950/1000) |
| Публикация | `publish.py` | `caption`, `image_url` | фото → текст → ЛС админу при ошибке |

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