# Этап 1, шаг 2: дедлайн прогона

Пункт 2 из [`stage1.md`](stage1.md). Цель — **один пост не должен съедать весь
бюджет времени цикла**. Без этого в режиме 24/7 один зависший источник или
медленный LLM сдвигает расписание, а при нескольких сбоях цикл перестаёт
успевать публиковать.

## Проблема

Один прогон может идти минутами:

- сбор 20+ источников, `retry_max_attempts: 5` на каждом, паузы до минуты на
  Reddit;
- два вызова LLM по 20–30с, плюс лестница бюджетов (повторы до 16384 токенов);
- Wikipedia с паузами 0.8с между запросами.

Живой замер: `timeout 60 python -m kino run` → `EXIT=124`, застрял на повторах
SlashFilm. Один медленный фид тормозит весь прогон.

## Решение: бюджет времени, а не жёсткий kill

`kill` на середине публикации опасен: пост может уйти в канал, а state не
записаться — на следующий прогон та же новость уйдёт дублем (пункт 5, этап 3).
Поэтому **не убиваем процесс**, а даём шагам понять, что пора закругляться.

Бюджет — объект `Deadline`, который отсчитывает время и умеет сказать, сколько
осталось. Шаги проверяют `expired` и `remaining` перед тем, как начать новую
работу; начатая работа не обрывается насильно.

### Приоритеты при исчерпании бюджета

1. **Опубликовать то, что уже готово** — важнее, чем добрать картинку или
   прогнать судью второй раз.
2. **Сбор** — первым отдаёт бюджет: если новости уже собраны, их можно
   обрабатывать.
3. **Картинка** — пропускается первой: пост без картинки лучше, чем пост
   никогда.
4. **Судья** — повтор по лестнице не запускается, если бюджета нет.

## Новый модуль `kino/deadline.py`

Чистая логика на `time.monotonic()` (не зависит от смены системного времени).

```python
"""Бюджет времени на прогон: мягкое завершение вместо зависания."""

from __future__ import annotations

import time


class Deadline:
    """Отсчитывает бюджет и умеет сказать, сколько осталось.

    Не убивает процесс, а даёт шагам понять, что пора закругляться:
    начатая работа (публикация) не обрывается насильно.
    """

    def __init__(self, total_seconds: float):
        if total_seconds is None or total_seconds <= 0:
            # Нет бюджета — значит, дедлайн не ограничивает.
            self.total = float("inf")
        else:
            self.total = float(total_seconds)
        self.started = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def remaining(self) -> float:
        return max(0.0, self.total - self.elapsed)

    @property
    def expired(self) -> bool:
        return self.remaining <= 0

    def slice(self, want: float) -> float:
        """Сколько можно потратить на шаг: min(want, remaining).

        Если осталось меньше, чем хотелось, — вернёт остаток. Если дедлайн
        не задан (inf) — вернёт want как есть.
        """
        if self.total == float("inf"):
            return want
        return min(want, self.remaining)

    def __repr__(self) -> str:
        return f"Deadline(remaining={self.remaining:.1f}s, total={self.total!r})"
```

### Вспомогательная функция для проверки

```python
def check(deadline: Deadline | None, *, step: str, cfg: object = None) -> bool:
    """True, если можно продолжать (бюджет не исчерпан).

    Централизует лог: «бюджет исчерпан на шаге X, останавливаемся».
    None-дедлайн возвращает True всегда (обратная совместимость).
    """
    if deadline is None or not deadline.expired:
        return True
    logger.warning("дедлайн прогона исчерпан (%.1fс) на шаге %s", deadline.total, step)
    return False
```

## Как дедлайн прошивается в шаги

### `pipeline.run(cfg, dry_run, deadline=None)`

Новая сигнатура. Если `deadline` не передан — создаёт из конфига
(`deadline_seconds`), т.е. обратная совместимость `run(cfg)` сохраняется.

Последовательность с проверками:

```python
deadline = deadline or Deadline(cfg.settings.deadline_seconds)

# сбор — первый кандидат на обрезку
if not check(deadline, step="сбор"):
    return _abort(...)
collected = collect(cfg, deadline=deadline)

# черновик
if not check(deadline, step="черновик"):
    return _abort(...)
draft_data = draft(choice, cfg, deadline=deadline)

# судья
if not check(deadline, step="судья"):
    return _abort(...)
final_data = judge(choice, draft_data, cfg, deadline=deadline)

# картинка — пропускается первой
if check(deadline, step="картинка"):
    image = find_image_multi(...)
else:
    image = ImageResult()

# публикация — НИКОГДА не отменяется по дедлайну
```

### `RunResult` — новые поля

```python
aborted: bool = False
abort_reason: str = ""
```

`_abort(reason)` возвращает `RunResult` с `aborted=True`, `published=False`,
пустой подписью и картинкой, плюс причину в `abort_reason`. `serve` видит
`aborted=True`, не считает это постом (счётчик не растёт) и логирует причину.

### `collect(cfg, deadline=None)`

- **Таймаут запроса** на источник: `slice(collect_timeout)` вместо
  фиксированного.
- **Повторы** (`_fetch_one`): перед каждой попыткой проверять `not expired`;
  пауза между попытками не превышает `remaining`.
- **Reddit-проход**: перед каждым источником проверять бюджет; если кончился —
  выйти, не дожидаясь следующего фида.
- Пустой результат при исчерпании бюджета → `CollectResult` с пустым `items`
  (не `RuntimeError`), а `run` видит `aborted`.

### `draft(choice, cfg, deadline=None)`

- **Таймаут** одного запроса: `slice(llm_timeout)`.
- **Лестница бюджетов**: не начинать следующую попытку, если `expired`; если
  бюджет кончился на середине лестницы — вернуть то, что спасли, или поднять
  `DraftError` с причиной «бюджет исчерпан», а не «не вышла ни одна попытка».

### `judge(choice, draft_data, cfg, deadline=None)`

- Таймаут: `slice(llm_timeout)`.
- Лестница `_judge_budgets` и фолбэк-модель: не начинать следующий шаг без
  бюджета. Если бюджет кончился — вернуть черновик как есть с
  `verdict="fallback"` и причиной «бюджет исчерпан» (это честнее, чем молча
  отключить судью).

### `image` — не трогаем таймауты

Wikipedia-запросы короткие и уже с паузами. Вместо того чтобы вплетать дедлайн
в каждый запрос, `pipeline` просто не вызывает `find_image_multi`, если бюджет
исчерпан. Это проще и безопаснее: картинка — самое низкоприоритетное.

## Конфиг

```yaml
run:
  deadline_seconds: 180      # общий бюджет на один пост
  collect_deadline_seconds: 90   # отдельно на сбор (запас на остальные шаги)
```

`Settings` получает поля `deadline_seconds: float = 180` и
`collect_deadline_seconds: float = 90`. Валидация: отрицательные → дефолт с
предупреждением.

## Тесты

`tests/test_deadline.py`:

- `Deadline(inf)` — `expired` всегда False, `slice(x)` возвращает x.
- `Deadline(60)` — `remaining` убывает, `expired` становится True после 60с
  (используя подмену `time.monotonic`).
- `slice` возвращает `min(want, remaining)`.
- Отрицательный/нулевой бюджет → `inf` (не ограничивает).

`tests/test_pipeline_deadline.py` (в `test_pipeline.py` или отдельно):

- `run(cfg, deadline=Deadline(0))` → `RunResult.aborted=True`, `published=False`,
  `publish` не вызван.
- Бюджет кончился после сбора → картинка пропущена, но если пост готов —
  публикуется.
- `deadline=None` → работает как раньше (обратная совместимость).
- `collect(cfg, deadline)` с исчерпанным бюджетом не делает сетевых запросов.

## Критерии «сделано»

1. `Deadline` корректен: `remaining`, `expired`, `slice`, `inf`-режим.
2. `run(cfg)` без `deadline` работает как раньше — все 281 тест зелёные.
3. `run(cfg, deadline=expired)` → `aborted=True`, причина в логе, публикации
   нет.
4. Медленный источник не съедает весь бюджет: сбор обрезается, остальные шаги
   успевают. Проверено живьём: `deadline_seconds=20` → прогон завершился на
   `aborted=True` ровно в 20с вместо зависания на SlashFilm.
5. Публикация никогда не отменяется по дедлайну: готовый пост уходит даже при
   нулевом бюджете на финальном шаге.
6. `serve` (шаг 5) может различать `aborted` и реальный пост.

## Итог шага 2 (реализовано)

- `kino/deadline.py` + `tests/test_deadline.py` (12 тестов).
- `kino/collect.py`: `collect(cfg, deadline=None)`, `_fetch_one`/`_fetch_cached`/
  `_fetch_once`/`_collect_reddit` принимают дедлайн; пауза между повторами
  ограничена бюджетом.
- `kino/draft.py`: `draft(choice, cfg, deadline=None)`, таймаут через
  `deadline.slice`, лестница не запускает следующий повтор без бюджета.
- `kino/judge.py`: `judge(choice, draft_data, cfg, deadline=None)`, таймаут +
  лестница + фолбэк обрываются по бюджету.
- `kino/pipeline.py`: `run(cfg, dry_run, deadline=None)`, `RunResult.aborted`/
  `abort_reason`, картинка пропускается при исчерпании, публикация — никогда.
- `config/settings.yaml`: секция `run.deadline_seconds: 180`.
- `tests/test_pipeline_deadline.py` (7 тестов).

Итог: **281 тест проходит** (`venv/bin/python -m pytest -q`).

## Границы

- **Не** вводим параллельность (асинхронность, потоки) — это другой этап.
- **Не** трогаем `image` внутренне — только пропуск вызова.
- **Не** делаем `kill`/`TimeoutExpired` на уровне процесса.
- **Не** меняем формат state в этом шаге (это шаг 3).