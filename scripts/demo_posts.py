"""Демонстрация генерации постов: новости → черновик → судья → подпись.

Ничего не публикует. Показывает полный цикл на живом LLM по всем форматам:
вариацию доставки (pick_variation/apply_infographic), формат-блоки подписи,
фолбэк инфографики в текст, приоритет фидовых картинок/видео.

Запуск:
    venv/bin/python scripts/demo_posts.py                  # все 16 форматов
    venv/bin/python scripts/demo_posts.py video review     # только указанные
"""

from __future__ import annotations

import json
import sys
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


def main() -> int:
    cfg = load_config()
    formats = sys.argv[1:] or sorted(cfg.weights)
    unknown = [f for f in formats if f not in cfg.weights]
    if unknown:
        print(f"нет таких форматов в config/formats.yaml: {unknown}")
        print(f"доступно: {sorted(cfg.weights)}")
        return 2

    print(f"модель: {cfg.draft_model} | судья: {cfg.judge_model}")
    print(f"форматов к показу: {len(formats)}")
    print("=" * 70)

    for index, fmt in enumerate(formats, start=1):
        item = NEWS[index % len(NEWS)]
        print(f"\n### ПОСТ {index}: формат={fmt} | {item.title}")
        print("-" * 70)
        choice = Choice(item=item, format=fmt, quality_score=5, sad_topic=False)

        try:
            draft_data = draft(choice, cfg)
        except DraftError as exc:
            print(f"  черновик не сгенерирован: {exc}")
            continue

        final = judge(choice, draft_data, cfg)
        verdict = str(final.get("verdict", ""))
        print(f"СУДЬЯ: verdict={verdict}")
        if verdict == "reject":
            print(f"  причина: {final.get('reason', 'не указана')}")
            continue

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

        format_fields = {
            k: final.get(k)
            for k in ("poll_options", "video_url", "infographic", "image_candidates",
                      "announcement", "review", "comparison", "riddle")
            if final.get(k) not in (None, "", [], {})
        }
        if format_fields:
            print("ФОРМАТ-ПОЛЯ:", json.dumps(format_fields, ensure_ascii=False))

        caption = build_caption(final, image.url, cfg, kind=kind,
                                include_facts=include_facts)
        print(f"\nГОТОВАЯ ПОДПИСЬ ({len(caption)} символов):")
        print(caption)
        print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())