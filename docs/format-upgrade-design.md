# Апгрейд 16 форматов — план реализации (design doc)

**Статус:** утверждён владельцем, к исполнению.
**Дата:** 18.10.2026.
**Область:** весь цикл `промпт → парсинг → caption → judge → тесты`.
**Правило:** на момент написания документа КОД НЕ ТРОГАЕТСЯ. Это только проект.

---

## 0. Утверждённые решения

| # | Решение | Выбор владельца |
|---|---------|-----------------|
| A | Разбить промпт на по-форматные спецификации | **ДА** — `prompts/formats/<fmt>.txt` |
| B | Как расширять схему JSON | **Вариант 1** — типизированные поля (не generic `blocks`) |
| R3 | Добавить `collage`/`comparison`/`behind_scenes` в `LONG_FORMATS` | **ДА** |
| Q1 | Механизм снятия выдуманного поля судьёй | **ДА** — `drop_fields` (судья явно снимает поле) |
| Q2 | Темп живых прогонов | **4 формата за сессию** (4 сессии × 4 формата) |

---

## 1. Контекст и baseline

В канале **16 форматов** контента. Реестр — `config/formats.yaml` (ключи `weights`,
сумма 110) и `docs/todo.md` («Реестр форматов (16)»). Форматы:

| # | format | вес | доставка |
|---|--------|-----|----------|
| 1 | facts | 20 | случайно |
| 2 | news | 15 | случайно |
| 3 | when | 10 | случайно |
| 4 | poll | 7 | poll |
| 5 | quote | 6 | случайно |
| 6 | trailer | 5 | link |
| 7 | video | 5 | link |
| 8 | infographic | 4 | photo (QuickChart) |
| 9 | collage | 5 | gallery |
| 10 | behind_scenes | 5 | случайно |
| 11 | interview | 5 | article |
| 12 | announcement | 5 | случайно |
| 13 | review | 5 | article |
| 14 | comparison | 4 | gallery |
| 15 | riddle | 4 | случайно |
| 16 | opinion | 5 | article |

Точки касания форматов в коде:

| Слой | Файл | Роль |
|------|------|------|
| Промпт | `prompts/draft_system.txt` (12.6 КБ) | каталог всех 16 форматов + общие поля + правила |
| Промпт | `prompts/judge_system.txt` (5.8 КБ) | общий фактчек + сохранение формат-полей |
| Промпт | `prompts/draft_user.txt`, `prompts/judge_user.txt` | подстановка `{format}`, `{draft}` |
| Загрузка | `kino/prompts.py` | `load_prompt`, `render`, дефолты |
| Выбор | `kino/choose.py` | `_pick_format`, `_SAD_SAFE` |
| Черновик | `kino/draft.py` | `build_prompt`, `_DRAFT_SALVAGE_KEYS`, лестница бюджетов |
| Парсинг | `kino/jsonparse.py` | фенсы → баланс → control-chars → salvage |
| Судья | `kino/judge.py` | `_SALVAGE_KEYS`, цикл переноса, guard пустых контейнеров |
| Подпись | `kino/caption.py` | `_format_block`, `_infographic_block`, `format_fields_plain`, `LONG_FORMATS` |
| Статья | `kino/pipeline.py::_publish_article` | тело статьи через `format_fields_plain` |
| Вариации | `kino/pipeline.py` | `_FORMAT_TO_KIND`, `pick_variation`, `_build_poll` |
| Тесты | `tests/test_formats.py` (44), `test_prompts.py`, `test_draft.py`, `test_jsonparse_salvage.py` | рендер, guard судьи, sad-фильтр, реестр |

Текущая схема JSON:
`headline, text, key_moment, question, hashtags, film, director, cast, release,
studio, wiki, poll_options, video_url, infographic{title,facts[{label,value}]},
image_candidates[], announcement{date,platform,what_to_expect},
review{verdict,pros,cons,rating},
comparison{item_a,item_b,categories[{name,winner,detail}]},
riddle{question_text,hint,answer}`.

---

## 2. Инварианты (что НЕ ломаем)

1. Формат-ключи не меняются: те же 16 строк, что в `formats.yaml.weights`.
2. Схема JSON остаётся «плоской» + вложенные dict/list (Вариант 1).
3. `_FORMAT_TO_KIND` (9 форсированных форматов) не меняется; 7 свободных остаются
   на `pick_variation`.
4. Guard судьи «пустой контейнер не затирает черновик» сохраняется.
5. `riddle.answer` не попадает в подпись и в статью — никогда.
6. Один стиль подписи (`kino.caption`) — второй рендерер не плодим.
7. Порядок `промпт → парсинг → caption → judge → тесты` соблюдается; каждый шаг =
   отдельный коммит + немедленный `git push origin main`.
8. `jsonparse.py` не меняется (salvage вложенных объектов не расширяем — см. R2).

---

## 3. Раскладка файлов после Фазы 0

```
prompts/
  draft_system.txt        ← СЖАТЬ: роль, общие поля, общие правила, базовый пример
  draft_user.txt          ← ДОБАВИТЬ {format_spec}
  judge_system.txt        ← СЖАТЬ: роль фактчекера + общие правила (починить склейку 8/9)
  judge_user.txt          ← ДОБАВИТЬ {format_spec}
  formats/                ← НОВАЯ ПАПКА, 16 файлов
    facts.txt
    news.txt
    when.txt
    poll.txt
    quote.txt
    trailer.txt
    video.txt
    infographic.txt
    collage.txt
    behind_scenes.txt
    interview.txt
    announcement.txt
    review.txt
    comparison.txt
    riddle.txt
    opinion.txt
```

Бюджет объёма (защита от распухания промпта, todo.md п.13 «следить за reasoning»):

| Файл | Сейчас | Цель |
|------|--------|------|
| `draft_system.txt` | 12.6 КБ | ≤ 4 КБ |
| `prompts/formats/*.txt` | — | ≤ 1.2 КБ каждый |
| **Итого в запрос** | **12.6 КБ** | **≈ 5–6 КБ** (system + один spec) |

Экономия ≈55% промпта на каждый вызов, при этом инструкций **по формату** становится
больше, а не меньше.

---

## 4. Промпт-слой: контракт `format_spec`

### 4.1 `kino/prompts.py` — API

Существующие `load_prompt`, `render`, `draft_system`, `draft_user`, `judge_system`,
`judge_user` — сохраняются. Добавляется:

| Элемент | Контракт |
|---------|----------|
| `FORMATS_DIR` | `ROOT / "prompts" / "formats"` |
| `GENERIC_SPEC` | встроенная заглушка: «формат-спецификация недоступна, следуй общему описанию» |
| `format_spec(fmt: str) -> str` | читает `formats/<fmt>.txt`; файла нет/пусто → `logger.error(...)` + `GENERIC_SPEC` (громко, не тихий дефолт) |
| `draft_user(**values)` | принимает `format_spec=...` (плейсхолдер `{format_spec}`) |
| `judge_user(**values)` | то же |

**Почему фолбэк, а не исключение:** пайплайн не должен падать в проде из-за пропавшего
файла (принцип уже заложен в `load_prompt`). Дрейф ловится тестом-реестром (Фаза 5),
а не тишиной в логе.

### 4.2 Единый шаблон spec-файла (одинаковые заголовки у всех 16)

```
# <format>
НАЗНАЧЕНИЕ:    одна фраза — зачем этот формат в канале.
СТРУКТУРА:     3–5 шагов, как строится пост.
ОБЯЗАТЕЛЬНО:   что должно быть (поля + минимальные пороги).
ЗАПРЕЩЕНО:     чего нельзя (специфика формата).
ПОЛЯ:          JSON-фрагмент только этого формата.
ПРИМЕР:        мини-JSON одного поля.
```

Одинаковые заголовки дают проверяемый инвариант: тест «в каждом из 16 файлов есть
все 6 секций».

### 4.3 Изменения в user-промптах

`draft_user.txt`:

```
Формат поста: {format}

--- СПЕЦИФИКАЦИЯ ФОРМАТА ---
{format_spec}
--- КОНЕЦ СПЕЦИФИКАЦИИ ---

Источник: {source}
Заголовок новости: {title}
Описание: {snippet}

Напиши пост строго по спецификации формата.
Верни JSON с полями: headline, text, key_moment, question, hashtags,
film, director, cast, release, studio, wiki,
<формат-специфичные поля — из спецификации>.
```

`judge_user.txt` — аналогично получает `{format_spec}`, чтобы судья знал, какие поля
обязаны быть и что в них нельзя выдумывать.

### 4.4 Точки вызова (сигнатуры не меняются)

- `kino/draft.py::build_prompt` — добавляет `format_spec=format_spec(choice.format)`.
- `kino/judge.py::build_judge_prompt` — то же.
- `choice.format` уже есть в `Choice` — новых зависимостей нет.

---

## 5. Схема данных: полный контракт полей (Вариант 1)

Формат записи: **ключ (тип, обязательность, потолок)** → рендер в подписи → правило судьи.

### 5.1 Новые ключи

| # | Формат | Ключ | Тип / лимиты | Рендер в подписи | Правило судьи |
|---|--------|------|--------------|------------------|---------------|
| 1 | facts | `fact_source` | str, опц., ≤120 | `📚 {fact_source}` | только из источника, иначе снять |
| 2 | news | `news` | `{context ≤160, whats_next ≤160, metric ≤80}`, части опц. | `🕐 Контекст:` · `➡️ Дальше:` · `📈 {metric}` | `metric` — цифра из источника |
| 5 | quote | `quote` | `{speaker ≤60, occasion ≤80, date ≤40}` | `🎙 {speaker} — {occasion}, {date}` | спикер/место/дата — из источника |
| 6 | trailer | `trailer` | `{highlights ≤3×≤120, release_window ≤80}` | `🎬 В трейлере: a · b · c` · `📅 Ждём:` | highlights — только из источника |
| 7 | video | `video` | `{timecodes ≤3×{time ≤8, note ≤80}, speakers ≤120}` | `⏱ 01:12 — …` · `👥 …` | таймкоды не выдуманы |
| 9 | collage | `collage` | `{theme ≤100, items ≤4×{title ≤80, note ≤120}}` | `🎞 {theme}` + `• title — note` | items без дублей, из источника |
| 10 | behind_scenes | `production` | `{problem ≤200, solution ≤200, numbers ≤120}` | `🎬` · `🛠` · `📊` | числа из источника |
| 11 | interview | `interview` | `[{q ≤160, a ≤300}]`, 3–4 пары | `**В:** …` / `**О:** …` | ответы только из источника |
| 16 | opinion | `opinion` | `{counterpoint ≤300, forecast ≤300, reader_takeaway ≤200}` | `⚖️` · `🔮` · `🎯` | `forecast` — прогноз, не факт |

### 5.2 Расширения существующих ключей

| # | Формат | Ключ | Что добавляется | Рендер | Правило судьи |
|---|--------|------|-----------------|--------|---------------|
| 3 | when | `announcement` | `+why_this_date ≤140` | `🗓 Почему эта дата: …` | не выдумано |
| 8 | infographic | `infographic.facts[]` | `+note ≤80` | `• {label} — {value} — {note}` | все цифры из источника |
| 12 | announcement | `announcement` | `+creator_quote ≤200`, `+backstory ≤200` | `💬 …` · `↩️ …` | цитата дословно |
| 13 | review | `review` | `+criteria ≤5×{name ≤30, score ≤12}`, `+audience ≤120`, `+similar_to ≤120` | `▫️ name — score` · `👤 Для кого:` · `≈ Похоже на:` | `rating` только из источника |
| 14 | comparison | `comparison` | `+verdict ≤160` | `🏁 Итог: …` | `winner` ∈ {a,b} |
| 15 | riddle | `riddle` | `hint` → `+hints ≤3×≤120`, `+riddle_type ≤40` | `💡 …` ×N | hints не содержат answer |

### 5.3 Обратная совместимость и осознанные умолчания

- `riddle.hint` (строка) продолжает читаться: если `hints` нет — рендерим `hint`.
- Миграции `state.json` / кэшей не нужны: все поля живут внутри одного прогона.
- **`poll_type` НЕ заводим** — ротация 4 подтипов остаётся prompt-only, схему не раздуваем.
  Статистика по подтипам — отдельная задача, если понадобится.
- Пустые контейнеры (`[]`, `{}`, `""`, `None`) в подписи не рендерятся.

---

## 6. Матрица улучшений по 16 форматам (2–3 аспекта × 5 слоёв)

> Формат ячейки: что именно добавляем. Все правки текста промптов идут в
> `prompts/formats/<fmt>.txt` (Фаза 1).

### 1. facts
- **Промпт:** (а) структура «Знали ли вы, что…» + контраст ожидание/реальность;
  (б) один яркий факт, а не пересказ новости; (в) обязательный `fact_source`.
- **Парсинг:** `fact_source` в `_DRAFT_SALVAGE_KEYS` и `_SALVAGE_KEYS`.
- **Caption:** блок `📚 …` после `text`, до `key_moment`.
- **Judge:** `fact_source` — только из источника, иначе снять; факт не должен быть
  пересказом news.
- **Тесты:** рендер `📚`; без источника блок не появляется; salvage строки.

### 2. news
- **Промпт:** (а) `news.context` — 1 фраза предыстории; (б) `news.whats_next` — «что
  дальше»; (в) `news.metric` — 1 число для масштаба.
- **Парсинг:** `news{}` в оба salvage-списка.
- **Caption:** три строки-блока (`🕐`, `➡️`, `📈`).
- **Judge:** `metric` — цифра обязана быть в источнике; `context` не выдуман.
- **Тесты:** рендер 3 блоков; частичный dict (только `whats_next`) не падает.

### 3. when
- **Промпт:** (а) акцент «когда и почему именно эта дата»; (б) разграничение с
  `announcement` (when = дата/причина, announcement = событие/ожидания);
  (в) `announcement.why_this_date`.
- **Парсинг:** `announcement` уже есть — расширяется, salvage не меняется.
- **Caption:** строка `🗓 Почему эта дата: …` в блоке announcement.
- **Judge:** дата и платформа — дословно из источника; «почему» не выдумано.
- **Тесты:** рендер нового поля; старая подпись `announcement` не сломана.

### 4. poll
- **Промпт:** (а) ротация 4 подтипов (ностальгия / вкус / прогноз / оценка);
  (б) варианты покрывают спектр, а не 4 синонима; (в) в `text` — интрига «итог/разбор
  в следующем посте».
- **Парсинг:** без новых полей.
- **Caption:** без изменений (вопрос подавляется при `kind=poll`).
- **Judge:** `poll_options` — конкретные названия, не «Да/Нет/Может быть»; иначе исправить
  или снять.
- **Тесты:** guard в `_build_poll` — отсев generic-вариантов, дедуп, лимит 6; опрос без
  вопроса → фолбэк `photo`.

### 5. quote
- **Промпт:** (а) `quote{speaker, occasion, date}`; (б) «на что отвечал / какая реакция»;
  (в) `image_candidates` с именем персоны (фото к цитате).
- **Парсинг:** `quote{}` в оба salvage-списка.
- **Caption:** `🎙 {speaker} — {occasion}, {date}`; фото персоны через `_pick_image`
  (добавить `quote.speaker` в кандидаты).
- **Judge:** цитата дословно; спикер/место/дата только из источника.
- **Тесты:** рендер `🎙`; экранирование кавычек; `quote.speaker` попадает в кандидаты картинки.

### 6. trailer
- **Промпт:** (а) разбор кадров и пасхалок (`trailer.highlights[]`); (б) «что
  подтвердилось из слухов, что удивило»; (в) `trailer.release_window`.
- **Парсинг:** `trailer{}` в оба salvage-списка.
- **Caption:** `🎬 В трейлере: …` + `📅 Ждём: …`; `video_url` остаётся для вариации `link`.
- **Judge:** `highlights` — только видимое в источнике; ссылка — только реальный URL.
- **Тесты:** рендер; трейлер без URL → фолбэк `photo` (уже есть) не сломан.

### 7. video
- **Промпт:** (а) структура «кто в кадре + о чём + ключевой момент»;
  (б) `video.timecodes[{time,note}]`; (в) фолбэк-расшифровка в `text`, если `video_url` пуст.
- **Парсинг:** `video{}` в оба salvage-списка.
- **Caption:** `⏱ 01:12 — …` и `👥 …`; при пустом `video_url` таймкоды остаются текстом.
- **Judge:** `timecodes` не выдуманы; «посмотри на YouTube» ссылкой не считается.
- **Тесты:** рендер таймкодов; без `video_url` пост не теряет смысл.

### 8. infographic
- **Промпт:** (а) `facts[].note` — почему это много/мало/рекорд; (б) сортировка: самый
  ударный факт первым; (в) «было → стало» для одной цифры.
- **Парсинг:** `note` внутри `infographic` — структура та же, salvage ок.
- **Caption:** `_infographic_block` → `• {label} — {value} — {note}`; лимит 5 фактов и
  раздельные срезы `label[:28]/value[:20]` сохранить (был баг с общим `[:70]`).
- **Judge:** все цифры — из источника; `note` не приписывает рекорды.
- **Тесты:** рендер `note`; длинная метка не съедает значение; URL QuickChart ≤ 2000.

### 9. collage
- **Промпт:** (а) `collage.theme` — объединяющая тема в headline; (б) `collage.items[{title,
  note}]` — мини-подписи; (в) «неожиданный элемент», ломающий шаблон.
- **Парсинг:** `collage{}` в оба salvage-списка.
- **Caption:** `🎞 {theme}` + строки items; `image_candidates` продолжает кормить галерею.
- **Judge:** items — из источника; нет дублей.
- **Тесты:** рендер; `theme` без items не рендерится; галерея < 2 картинок → фолбэк `photo`.

### 10. behind_scenes
- **Промпт:** (а) драматургия «проблема на площадке → как решали → результат»;
  (б) `production.numbers` — конкретика (дубли, бюджет сцены, дни); (в) имена «невидимых
  героев» (оператор, художник, каскадёр).
- **Парсинг:** `production{}` в оба salvage-списка.
- **Caption:** три блока `🎬` / `🛠` / `📊`.
- **Judge:** числа из источника; имена — из источника, не «придуманный оператор».
- **Тесты:** рендер 3 блоков; частичный dict.

### 11. interview
- **Промпт:** (а) `interview[{q,a}]` вместо markdown-хака `**В:**/**О:**`; (б) драматургия:
  первый вопрос острый, последний — личный/блиц; (в) контекст встречи (где/когда) в `text`.
- **Парсинг:** `interview[]` в оба salvage-списка (вложенные объекты salvage не тянет — см. R2).
- **Caption:** пары `**В:** …` / `**О:** …` из поля; `text` — только лид. `LONG_FORMATS`
  уже включает interview.
- **Judge:** ответы — только из источника; вопросы не приписывают реплики.
- **Тесты:** рендер пар; пустой `interview` → рендер из `text` (регрессия); длинный формат
  не режется капом 950.

### 12. announcement
- **Промпт:** (а) `creator_quote` — цитата из пресс-релиза; (б) `backstory` — как проект
  дошёл до анонса; (в) «почему важно именно сейчас».
- **Парсинг:** расширение существующего `announcement` (salvage не меняется).
- **Caption:** `💬 …` и `↩️ …` в блоке announcement.
- **Judge:** цитата дословно; дата/платформа не выдуманы (правило уже есть).
- **Тесты:** рендер; старые тесты announcement зелёные.

### 13. review
- **Промпт:** (а) `review.criteria[{name, score}]` — сценарий/игра/визуал/темп/звук;
  (б) `audience` — «для кого»; (в) `similar_to` — «похоже на X» (только из источника).
- **Парсинг:** расширение `review` (salvage ок).
- **Caption:** строки `▫️ {name} — {score}`, `👤 Для кого:`, `≈ Похоже на:`; `rating` как сейчас.
- **Judge:** **`rating` — только если явно в источнике, иначе `drop_fields: ["review.rating"]`**;
  `criteria.score` — из источника.
- **Тесты:** `rating` не из источника → снят → в подписи нет скобок с оценкой; рендер criteria.

### 14. comparison
- **Промпт:** (а) `comparison.verdict` — кому что выбрать; (б) минимум 1 категория, где
  фаворит проигрывает; (в) не сравнивать несравнимое (пропускать категорию).
- **Парсинг:** расширение `comparison` (salvage ок).
- **Caption:** `🏁 Итог: …` после строк категорий.
- **Judge:** `winner` только `a`/`b`; `detail` из источника.
- **Тесты:** рендер итога; неизвестный `winner` не ломает строку (сейчас `mark` = '').

### 15. riddle
- **Промпт:** (а) `riddle.hints[]` — 2–3 подсказки по возрастанию; (б) подформаты
  (`riddle_type`: «угадай фильм» / «правда или миф» / «чей кадр» / «фильм по эмодзи»);
  (в) `answer` не публикуется.
- **Парсинг:** `hints[]` в оба salvage-списка; `hint` оставить для обратной совместимости.
- **Caption:** `💡 …` по каждой подсказке; `answer` по-прежнему не читается.
- **Judge:** правила 9–11 сохранить; добавить проверку, что `hints` не содержит `answer`.
- **Тесты:** утечка ответа через судью (регрессия), рендер 2–3 подсказок, `riddle_type` не
  влияет на подпись.

### 16. opinion
- **Промпт:** (а) `opinion.counterpoint` — другая точка зрения; (б) `opinion.forecast` —
  прогноз; (в) `opinion.reader_takeaway` — «что это значит для зрителя».
- **Парсинг:** `opinion{}` в оба salvage-списка.
- **Caption:** `⚖️` / `🔮` / `🎯` блоками; `LONG_FORMATS` уже включает opinion.
- **Judge:** `counterpoint` — не выдуманный оппонент; `forecast` помечен как прогноз.
- **Тесты:** рендер; длинный формат > 3000 симв. и ≤ 4096 с хвостом (есть шаблон).

---

## 7. Слой парсинга (`jsonparse` + salvage)

`kino/jsonparse.py` — **не меняется**.

| Файл | Правка |
|------|--------|
| `kino/draft.py` | `_DRAFT_SALVAGE_KEYS` += `fact_source, news, quote, trailer, video, collage, production, interview, opinion` |
| `kino/judge.py` | `_SALVAGE_KEYS` += те же 9 ключей |
| `kino/judge.py` | цикл переноса полей += те же 9 ключей (guard «пустое не затирает» сохраняется) |

Скалярные поля (`fact_source`) спасаются существующей строковой веткой; dict/список —
только полным разбором (salvage их не тянет — осознанно).

---

## 8. Слой caption (`kino/caption.py`)

### 8.1 Новый механизм судьи: `drop_fields`

Проблема: сейчас судья **не может удалить** поле — `None`/`""`/`[]`/`{}` пропускаются,
чтобы не затирать черновик. А нужно уметь снять выдуманный `rating`.

Решение: новое поле ответа судьи `drop_fields: ["review.rating", "video_url", ...]`
(список путей). `judge.py` после переноса **удаляет** указанные пути. Поддержка одного
уровня `parent.child`; некорректный путь игнорируется с warning. Пустой контейнер
по-прежнему не затирает — это **разные** механизмы.

Тест: `drop_fields` снимает путь; пустой контейнер — нет (существующий тест остаётся зелёным).

### 8.2 `_format_block` — канонический порядок блоков

Фиксированный порядок (нужен для детерминированных тестов; в реальном посте заполнен
только один формат):

```
1.  announcement   (date · platform → why_this_date → what_to_expect → creator_quote → backstory)
2.  news           (context → whats_next → metric)
3.  quote          (speaker line)
4.  trailer        (highlights → release_window)
5.  video          (speakers → timecodes)
6.  production     (problem → solution → numbers)
7.  interview      (пары В/О)
8.  review         (verdict+rating → criteria → pros → cons → audience → similar_to)
9.  comparison     (item_a vs item_b → rows → verdict)
10. collage        (theme → items)
11. riddle         (hints; answer не читается)
12. opinion        (counterpoint → forecast → reader_takeaway)
13. facts          (fact_source — последним, как подпись к факту)
```

### 8.3 Два независимых потолка — важный нюанс для R3

`LONG_FORMATS` снимает **редакторский** кап 950, но **не** потолок Telegram:

| Формат | Вариация | Потолок |
|--------|----------|---------|
| interview / review / opinion | article → link / text | **4096** |
| collage / comparison | **gallery** (фиксировано!) | **1024** |
| behind_scenes | любая (7 свободных) | 1024 (photo/gallery) или 4096 (text/link/poll) |

Вывод: для `collage`/`comparison` добавление в `LONG_FORMATS` поднимает лимит с 950 до
1024 — и всё. Чтобы содержимое не резалось, вводим **лимиты по количеству элементов**
(в схеме п.5.1: items ≤4, categories ≤4, highlights ≤3, criteria ≤5) — они гарантируют,
что галерейный пост влезает в 1024 без обрезки. Это причина, почему лимиты жёсткие.

`behind_scenes` — реальный выигрыш: в text/link-вариациях получает 4096.

Изменение одной строки:
`LONG_FORMATS = {"interview", "review", "opinion", "collage", "comparison", "behind_scenes"}`

### 8.4 Статья (`format_fields_plain`) и `_publish_article`

`pipeline._publish_article` **не меняется**: он уже зовёт `format_fields_plain(draft_data)`,
а та вызывает `_format_block(plain=True)`. Все новые блоки автоматически поедут в тело
статьи graph.org с правильным экранированием (`plain=True` — без `&amp;`).

---

## 9. Слой judge (правила)

`judge_system.txt` (сжатый) + `{format_spec}` в `judge_user`. Общие правила 1–7, 10, 11
остаются; добавляются:

| Правило | Суть |
|---------|------|
| J1 | `rating` (review) — только если явно в источнике; иначе `drop_fields: ["review.rating"]` |
| J2 | `poll_options` — конкретные названия; «Да/Нет/Может быть/Свой вариант» недопустимы → исправить или снять |
| J3 | `infographic.facts[].value/note` — каждая цифра из источника |
| J4 | `video.timecodes` / `trailer.highlights` — только то, что есть в источнике |
| J5 | `riddle.hints` не должны содержать `answer` (расширение правил 9–11) |
| J6 | `quote.speaker/occasion/date`, `announcement.date/platform` — дословно из источника |
| J7 | `comparison.winner` ∈ {a, b}; не сравнивать несравнимое |
| J8 | **починка:** правила 8 и 9 сейчас склеены в одну строку — разнести |
| J9 | `drop_fields` — описать как штатный инструмент снятия выдуманного поля |

---

## 10. Тесты

### 10.1 Новые файлы

| Файл | Что проверяет |
|------|---------------|
| `tests/test_format_registry.py` | **Единый источник правды / дрейф:** ключи `formats.yaml.weights` == файлы `prompts/formats/*.txt` == `_FORMAT_TO_KIND` ∪ свободные; каждый spec имеет 6 секций; все ключи схемы известны salvage-спискам и рендерятся. Ловит дрейф уровня todo.md п.25 |
| `tests/test_format_fields.py` | Контракт полей: лимиты (highlights ≤3, items ≤4, criteria ≤5…), обязательность, обратная совместимость `riddle.hint`→`hints` |

### 10.2 Дополнения в существующие

| Файл | Тесты |
|------|-------|
| `tests/test_prompts.py` | `format_spec` есть файл; нет файла → `GENERIC_SPEC` + ERROR; `{format_spec}` подставляется в user-промпт |
| `tests/test_formats.py` | рендер каждого нового блока (16); escape HTML; пустой контейнер не рендерится; `drop_fields` снимает путь; `rating` не из источника → нет в подписи; poll-опции generic → замена; hints без утечки answer; LONG_FORMATS: collage/comparison ≤1024 в gallery, behind_scenes >3000 в text |
| `tests/test_draft.py` | salvage новых ключей из обрезанного JSON |
| `tests/test_jsonparse_salvage.py` | `fact_source` (строка) спасается; dict-ключи — нет (фиксируем поведение) |

### 10.3 Обновления

- `test_registry_forced_formats_match_parametrized_list` — набор не меняется (9 форсированных);
  проверить, что не разъехался.
- `test_judge_empty_containers_do_not_wipe_draft` — добавить новые ключи в перенос.

**Прогон:** `pytest -q` целиком, вывод в чат (сейчас 375 → ожидаю ~440–460).

---

## 11. Порядок внедрения и коммиты

| Фаза | Коммит | Содержимое | Ожидаемо тестов |
|------|--------|------------|-----------------|
| 0 | `refactor(prompts): per-format spec files` | `format_spec`, 16 файлов-заглушек, плейсхолдеры, вызовы, `test_prompts` | 375 → ~385 |
| 1 | `feat(prompts): 16 format specs` | содержание спецификаций (2–3 улучшения на формат), сжатие system | ~385 |
| 2 | `feat(schema): format fields + salvage` | `_DRAFT_SALVAGE_KEYS`, `_SALVAGE_KEYS`, transfer-цикл, `drop_fields` | ~400 |
| 3 | `feat(caption): render new blocks` | `_format_block` (13 ветвей), `LONG_FORMATS` | ~425 |
| 4 | `feat(judge): per-format rules` | `judge_system` + spec-инъекция + J1–J9 | ~440 |
| 5 | `test: format registry drift guard` | `test_format_registry`, `test_format_fields`, запись в `todo.md` | ~450 |
| 6 | `docs: live run notes` | dry-run по форматам, разбор логов | — |

**После каждой фазы:** `git commit` + немедленный `git push origin main`
(правило: 18.10 накопилось 19 непушенных коммитов — сервер работал на старом коде).

---

## 12. Критерии приёмки

1. `pytest -q` — 100% зелёных, число тестов ≥ 440.
2. Каждый из 16 форматов: есть spec-файл, ≥1 тест на рендер, ≥1 тест на правило судьи.
3. `test_format_registry` зелёный — дрейфа нет.
4. Промпт на вызов ≤ 6 КБ (было 12.6 КБ).
5. Живой dry-run по форматам: в логе видно формат, вариацию, поля; утечек `answer` нет.
6. Ни одного `push`-долга (все коммиты ушли в origin).

---

## 13. Риски

| Риск | Митигация |
|------|-----------|
| R1. Промпт всё равно распухнет | Бюджет в п.3 + тест на 6 секций + сжатие system |
| R2. Salvage вложенных объектов (`interview[]`, `criteria[]`) не тянет | Критичные данные дублируются в `text` (правило 9); salvage не расширяем — фиксируем тестом |
| R3. Gallery 1024 vs LONG_FORMATS | Жёсткие лимиты по количеству элементов (п.8.3) |
| R4. `drop_fields` разрастётся | Только один уровень `parent.child`, тест на игнор некорректного пути |
| R5. Бюджет LLM | 4 формата за сессию (см. п.14) |

---

## 14. План живых прогонов (4 формата за сессию)

Лимит LLM: 50 credits MAPIMart/неделю (≈$5). 16 форматов × (черновик + судья) = 32 вызова.
Дробим на 4 сессии по 4 формата (по 8 вызовов каждая):

| Сессия | Форматы | Что смотрим в первую очередь |
|--------|---------|------------------------------|
| 1 | facts, news, when, quote | `fact_source`, `news{}`, `announcement.why_this_date`, `quote{}` |
| 2 | poll, trailer, video, infographic | poll-опции, `trailer.highlights`, `video.timecodes`, `facts[].note` |
| 3 | collage, behind_scenes, interview, announcement | `collage{}`, `production{}`, пары В/О, `creator_quote`/`backstory` |
| 4 | review, comparison, riddle, opinion | `rating`-guard, `comparison.verdict`, hints без утечки, `opinion{}` |

Каждая сессия: dry-run, разбор лога (формат, вариация, поля, вердикт судьи), запись находок
в `docs/todo.md`.

---

## 15. Приложения

### 15.1 Пример заполненного черновика (review, после апгрейда)

```json
{
  "headline": "«Дюна: Часть третья» — масштаб есть, темпа нет",
  "text": "Финал трилогии Вильнёва доводит сагу до точки, но разгоняется слишком долго. В тексте — главный вердикт и ключевые цифры: 8/10 по версии источника.",
  "key_moment": "🔥 Тимоти Шаламе и Зендея вернулись к ролям, съёмки заняли 128 дней.",
  "question": "Как думаете, нужен ли триквел ещё один фильм по вселенной?",
  "hashtags": ["Дюна", "Вильнёв", "кино"],
  "film": "Дюна: Часть третья",
  "director": "Дени Вильнёв",
  "cast": "Тимоти Шаламе, Зендея",
  "release": "18 декабря 2026",
  "studio": "Legendary",
  "wiki": "https://ru.wikipedia.org/wiki/Дюна:_Часть_три",
  "review": {
    "verdict": "Стоит смотреть ради финала",
    "rating": "8/10",
    "pros": ["визуал", "музыка"],
    "cons": ["затянутый первый акт"],
    "criteria": [
      {"name": "Сценарий", "score": "7/10"},
      {"name": "Визуал", "score": "10/10"},
      {"name": "Темп", "score": "6/10"}
    ],
    "audience": "Фанатам саги и любителям масштабной фантастики",
    "similar_to": "«Бегущий по лезвию 2049»"
  },
  "poll_options": [],
  "video_url": "",
  "infographic": null,
  "image_candidates": [],
  "announcement": null,
  "comparison": null,
  "riddle": null
}
```

### 15.2 Пример ответа судьи с `drop_fields`

```json
{
  "verdict": "accept",
  "headline": "«Дюна: Часть третья» — масштаб есть, темпа нет",
  "text": "…",
  "drop_fields": ["review.rating"],
  "reason": "",
  "confidence": 0.86
}
```

### 15.3 Чеклист фазы (шаблон для каждой из 0–5)

- [ ] Пересказ задачи в 2–3 строках.
- [ ] План: какие файлы трогаю и зачем.
- [ ] Правка: один файл за раз, минимально, форматы входов/выходов сохранены.
- [ ] Тест: запуск + вывод целиком (без «всё ок» без вывода).
- [ ] Отчёт: сделано / проверено / осталось.
- [ ] Git: `commit` + `push origin main`.
- [ ] `docs/todo.md`: вычеркнуть закрытое, дописать новые находки.