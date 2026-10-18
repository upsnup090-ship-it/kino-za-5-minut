"""Демонстрация генерации постов: несколько новостей → черновик → судья → подпись.

Не публикует ничего. Показывает полный цикл создания поста на живом LLM.

Запуск:
    venv/bin/python scripts/demo_posts.py
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
from kino.image import find_image_multi  # noqa: E402
from kino.judge import judge  # noqa: E402
from kino.pipeline import build_caption  # noqa: E402

NEWS = [
    FeedItem(
        source="Variety",
        title="Dune Part Three officially announced by Denis Villeneuve",
        link="https://variety.com/dune3",
        pub_date="",
        ts=0.0,
        snippet="Denis Villeneuve confirms he will return to direct the third Dune film.",
    ),
    FeedItem(
        source="Deadline",
        title="New Superman trailer drops online",
        link="https://deadline.com/superman-trailer",
        pub_date="",
        ts=0.0,
        snippet="James Gunn unveils the first full trailer for the new Superman movie.",
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
    print(f"модель: {cfg.draft_model} | судья: {cfg.judge_model}")
    print("=" * 70)

    for index, item in enumerate(NEWS, start=1):
        print(f"\n### ПОСТ {index}: {item.title}")
        print("-" * 70)
        choice = Choice(item=item, format="news", quality_score=5, sad_topic=False)

        try:
            draft_data = draft(choice, cfg)
        except DraftError as exc:
            print(f"  черновик не сгенерирован: {exc}")
            continue

        print("ЧЕРНОВИК:")
        print(json.dumps(draft_data, ensure_ascii=False, indent=2))

        final = judge(choice, draft_data, cfg)
        print(f"\nСУДЬЯ (verdict: {final.get('verdict')}):")
        print(json.dumps(final, ensure_ascii=False, indent=2))

        wiki_title = str(final.get("wiki", "")).strip()
        image = find_image_multi([wiki_title], cfg) if wiki_title else None
        image_url = image.url if image else ""
        print(f"\nКАРТИНКА: {image_url or 'нет'}")

        caption = build_caption(final, image_url, cfg)
        print(f"\nГОТОВАЯ ПОДПИСЬ ({len(caption)} символов):")
        print(caption)
        print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())