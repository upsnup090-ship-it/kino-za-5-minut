"""Постинг по кнопке: тестовый в личку, финальный в канал.

Прогон долгий (сбор + LLM + судья), поэтому уходит в отдельный поток, а
кнопка сразу отвечает «запущено». Второй тап во время прогона получает
отказ: два поста в канал — не то, чего ждёт человек, нажавший дважды.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from kino import posting
from kino.config import Config

from kino.admin.common import Reply, _btn
from kino.admin.screens import screen_home

logger = logging.getLogger(__name__)

# --- постинг по кнопке -------------------------------------------------------


def handle_post_button(cfg: Config, mode: str, now: datetime | None = None) -> Reply:
    """Кнопка постинга: `test` — в личку, `final` — в канал.

    Прогон долгий (сбор + LLM + судья, до `deadline_seconds`), поэтому он
    уходит в отдельный поток, а кнопка сразу отвечает «запущено». Бот не
    замирает, Telegram не отдаёт таймаут, кнопка не залипает.

    Второй тап во время прогона получает отказ: два поста в канал — это не
    то, чего ждёт человек, нажавший кнопку дважды.
    """
    if mode not in ("test", "final"):
        return screen_home(cfg)

    if not posting.STATE.begin(mode):
        return Reply(
            text=(
                f"⏳ Уже идёт прогон ({posting.STATE.mode}).\n"
                "Дождись его — второй пост в канал не нужен."
            ),
            keyboard=[[_btn("🏠 Домой", "home")]],
        )

    where = "в личку" if mode == "test" else "в канал"
    logger.info("постинг по кнопке: %s %s", mode, where)

    def _worker() -> None:
        from kino import tg_api

        try:
            if mode == "test":
                prepared = posting.build_only(cfg)
                head = posting.preview_text(prepared, to_channel=False)
                tg_api.send_message(cfg, cfg.admin_chat_id, head)
                if prepared.ok:
                    ok = posting.deliver(cfg, prepared, cfg.admin_chat_id)
                    if not ok:
                        tg_api.send_message(
                            cfg, cfg.admin_chat_id,
                            "⚠️ Пост собран, но отправить не удалось — смотри лог.",
                        )
                else:
                    tg_api.send_message(cfg, cfg.admin_chat_id, head)
            else:
                result = posting.publish_now(cfg)
                tg_api.send_message(
                    cfg, cfg.admin_chat_id,
                    posting.describe_result(result, to_channel=True),
                )
        except Exception as exc:  # noqa: BLE001 — поток не должен умирать молча
            logger.exception("постинг по кнопке упал: %s", exc)
            try:
                tg_api.send_message(
                    cfg, cfg.admin_chat_id, f"❌ Прогон упал: {exc.__class__.__name__}"
                )
            except Exception:  # noqa: BLE001
                logger.error("не удалось сообщить о падении прогона")
        finally:
            posting.STATE.end()

    threading.Thread(target=_worker, name=f"post-{mode}", daemon=True).start()

    if mode == "test":
        text = "🧪 Запустил тестовый пост. Соберу и пришлю сюда же — в канал не пойдёт."
    else:
        text = "🚀 Запустил финальный пост. Уйдёт в канал, как по расписанию."
    return Reply(text=text, keyboard=[[_btn("🏠 Домой", "home")]])

