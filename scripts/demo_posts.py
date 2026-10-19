"""Живая сессия: новости → черновик → судья → подпись → отчёт по полям.

Ничего не публикует. Показывает полный цикл на живом LLM по всем форматам:
вариацию доставки (pick_variation/apply_infographic), формат-блоки подписи,
фолбэк инфографики в текст, приоритет фидовых картинок/видео.

Для Фазы 6 («качество текста») добавлен отчёт по каждому формату:

* **ПОЛЯ** — ожидались / заполнены / пусто. Ожидаемый состав берётся из секции
  «ПОЛЯ:» спецификации формата (kino.prompts.expected_fields), то есть из того
  же файла, что уходит модели. Недоданное поле — кандидат в находки.
* **ОБРЕЗКА** — перехват WARNING от kino.draft/kino.jsonparse: ответ упёрся в
  max_tokens, какие поля спаслись.
* **УТЕЧКА** — для riddle: не попал ли `riddle.answer` в готовую подпись.
* **СВОДКА** — таблица формат × (вердикт, поля, длина подписи, обрезка, утечка)
  и список форматов с недоданными полями: из неё сразу пишутся находки в todo.

Запуск (сессия = 4 формата, как в дизайн-доке, п.14):
    venv/bin/python scripts/demo_posts.py facts news when quote
    venv/bin/python scripts/demo_posts.py video review     # только указанные
    venv/bin/python scripts/demo_posts.py                  # все 16 форматов
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kino.choose import Choice  # noqa: E402
from kino.collect import FeedItem  # noqa: E402
from kino.config import load_config  # noqa: E402
from kino.draft import DraftError, draft  # noqa: E402
from kino.judge import judge  # noqa: E402
from kino.pipeline import (  # noqa: E402
    _FORMAT_TO_KIND,
    _VARIATIONS,
    _pick_image,
    apply_infographic,
    build_caption,
    pick_variation,
)
from kino.prompts import expected_fields  # noqa: E402

NEWS = [
    FeedItem(
        source="Variety",
        title="Dune Part Three officially announced by Denis Villeneuve",
        link="https://variety.com/dune3",
        pub_date="",
        ts=0.0,
        snippet="Denis Villeneuve confirms he will return to direct the third Dune film.",
        images=["https://variety.com/examples/dune-poster.jpg"],
    ),
    FeedItem(
        source="Deadline",
        title="New Superman trailer drops online",
        link="https://deadline.com/superman-trailer",
        pub_date="",
        ts=0.0,
        snippet="James Gunn unveils the first full trailer for the new Superman movie.",
        video_url="https://www.youtube.com/watch?v=superman123",
        images=["https://i.ytimg.com/vi/superman123/hqdefault.jpg"],
    ),
    FeedItem(
        source="THR",
        title="Oscar 2027: early frontrunners revealed",
        link="https://hollywoodreporter.com/oscars-2027",
        pub_date="",
        ts=0.0,
        snippet="Awards season analysts name the early contenders for Best Picture.",
    ),
]

#: Значения, которые считаем «поле не заполнено».
_EMPTY = (None, "", [], {})

#: Признаки обрезки/salvage в WARNING-логах прогона.
_TRUNCATION_MARKERS = ("обрезан", "спасены поля", "уперлись в лимит")


def _is_filled(value: object) -> bool:
    """Заполнено ли формат-поле (не пустая строка/массив/объект)."""
    return value not in _EMPTY


class _LogCapture(logging.Handler):
    """Копит WARNING+ за один прогон, чтобы показать обрезку и salvage."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            self.messages.append(record.getMessage())
        except Exception:  # noqa: BLE001 — отчёт не должен падать из-за лога
            pass

    def reset(self) -> None:
        self.messages.clear()

    def truncation_notes(self) -> list[str]:
        return [m for m in self.messages if any(k in m for k in _TRUNCATION_MARKERS)]


@dataclass
class Row:
    """Итог одного формата для сводки сессии."""

    fmt: str
    expected: list[str] = field(default_factory=list)
    verdict: str = ""
    filled: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    caption_len: int = 0
    truncated: bool = False
    leak: bool = False
    error: str = ""


def _riddle_answer_leaks(final: dict, caption: str) -> bool:
    """Попал ли riddle.answer в подпись (критерий приёмки дизайн-дока, п.5)."""
    riddle = final.get("riddle")
    if not isinstance(riddle, dict):
        return False
    answer = str(riddle.get("answer") or "").strip()
    if not answer:
        return False
    return answer.lower() in caption.lower()


def _print_fields_report(row: Row, final: dict) -> None:
    """Печатает «ожидались / заполнены / пусто» и превью значений."""
    if not row.expected:
        print("ПОЛЯ: спецификация не дала ожидаемых ключей — сверка пропущена")
        return
    print(f"ПОЛЯ: ожидались {row.expected}")
    print(f"      заполнены {row.filled or '—'} | пусто {row.missing or '—'}")
    for key in row.expected:
        value = final.get(key)
        if _is_filled(value):
            preview = json.dumps(value, ensure_ascii=False)
            if len(preview) > 140:
                preview = preview[:137] + "..."
            print(f"      {key}: {preview}")


def _run_format(index: int, fmt: str, cfg, capture: _LogCapture) -> Row:
    """Один формат: черновик → судья → доставка → подпись → отчёт."""
    item = NEWS[index % len(NEWS)]
    row = Row(fmt=fmt, expected=expected_fields(fmt))
    print(f"\n### ПОСТ {index}: формат={fmt} | {item.title}")
    print("-" * 70)
    choice = Choice(item=item, format=fmt, quality_score=5, sad_topic=False)
    capture.reset()

    try:
        draft_data = draft(choice, cfg)
    except DraftError as exc:
        row.error = str(exc)
        print(f"  черновик не сгенерирован: {exc}")
        return row

    final = judge(choice, draft_data, cfg)
    row.verdict = str(final.get("verdict", ""))
    print(f"СУДЬЯ: verdict={row.verdict}")
    if row.verdict == "reject":
        print(f"  причина: {final.get('reason', 'не указана')}")

    # Полный маршрут доставки, как в run(): картинка → вариация → график.
    image = _pick_image(choice, final, str(final.get("wiki", "")).strip(), cfg)
    kind, link_url = pick_variation(cfg, final, image.url, choice)
    image, include_facts = apply_infographic(choice, final, image)

    dictated = _FORMAT_TO_KIND.get(fmt, "случайно")
    print(f"ДОСТАВКА: kind={kind} | формат диктует: {dictated} "
          f"(все вариации: {', '.join(_VARIATIONS)})")
    if link_url:
        print(f"ССЫЛКА: {link_url}")
    if image.url:
        print(f"КАРТИНКА: {image.url}  ({image.lang})")

    row.filled = [k for k in row.expected if _is_filled(final.get(k))]
    row.missing = [k for k in row.expected if k not in row.filled]
    _print_fields_report(row, final)

    notes = capture.truncation_notes()
    row.truncated = bool(notes)
    print(f"ОБРЕЗКА: {'есть' if notes else 'нет'}")
    for note in notes:
        print(f"  • {note}")

    caption = build_caption(final, image.url, cfg, kind=kind,
                            include_facts=include_facts, fmt=fmt)
    row.caption_len = len(caption)
    if fmt == "riddle":
        row.leak = _riddle_answer_leaks(final, caption)
        if row.leak:
            print("⚠ УТЕЧКА: riddle.answer найден в подписи")
    print(f"\nГОТОВАЯ ПОДПИСЬ ({len(caption)} символов):")
    print(caption)
    print("=" * 70)
    return row


def _print_summary(rows: list[Row]) -> None:
    """Таблица сессии + форматы, где модель недодала поля."""
    print("\n" + "=" * 70)
    print("СВОДКА СЕССИИ")
    print("=" * 70)
    header = f"{'формат':<14} {'вердикт':<10} {'поля':<8} {'подпись':>8}  обрезка  утечка"
    print(header)
    print("-" * len(header))
    for row in rows:
        if row.error:
            print(f"{row.fmt:<14} {'ошибка':<10} {'—':<8} {'—':>8}  —        —")
            continue
        fields = f"{len(row.filled)}/{len(row.expected)}" if row.expected else "—"
        print(f"{row.fmt:<14} {row.verdict or '—':<10} {fields:<8} {row.caption_len:>8}  "
              f"{'есть' if row.truncated else 'нет':<8} {'ЕСТЬ' if row.leak else 'нет'}")

    rejects = [r.fmt for r in rows if r.verdict == "reject"]
    errors = [r.fmt for r in rows if r.error]
    missing = {r.fmt: r.missing for r in rows if r.missing}
    print()
    print(f"форматов: {len(rows)} | reject: {len(rejects)} {rejects or ''} | "
          f"ошибок: {len(errors)} {errors or ''}")
    if missing:
        print("недоданы поля (кандидаты в находки todo.md):")
        for fmt, keys in missing.items():
            print(f"  {fmt}: {keys}")
    leaks = [r.fmt for r in rows if r.leak]
    if leaks:
        print(f"⚠ утечка riddle.answer: {leaks}")


def main() -> int:
    cfg = load_config()
    formats = sys.argv[1:] or sorted(cfg.weights)
    unknown = [f for f in formats if f not in cfg.weights]
    if unknown:
        print(f"нет таких форматов в config/formats.yaml: {unknown}")
        print(f"доступно: {sorted(cfg.weights)}")
        return 2

    print(f"модель: {cfg.draft_model} | судья: {cfg.judge_model}")
    print(f"форматов в сессии: {len(formats)}")
    print("=" * 70)

    capture = _LogCapture()
    root = logging.getLogger()
    root.addHandler(capture)
    rows: list[Row] = []
    try:
        for index, fmt in enumerate(formats, start=1):
            rows.append(_run_format(index, fmt, cfg, capture))
    finally:
        root.removeHandler(capture)

    _print_summary(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())