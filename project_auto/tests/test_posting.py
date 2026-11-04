"""Тесты постинга по кнопке: сборка без публикации, отправка, замок прогона.

Сеть не нужна: `pipeline.run` и функции `publish` подменяются моками.
Проверяется то, ради чего модуль существует, — что кнопка и расписание
делают одно и то же, и что второй тап не запускает второй прогон.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from kino import posting
from kino.config import Config, Settings
from kino.pipeline import RunResult


def _cfg(tmp_path: Path, admin_chat_id: str = "-100500") -> Config:
    return Config(
        sources=[], weights={"news": 1}, bad=[], sad=[],
        settings=Settings(
            schedule_catchup_minutes=30,
            schedule_late_grace_minutes=20,
            timezone="Europe/Moscow",
        ),
        admin_chat_id=admin_chat_id,
        state_path=tmp_path / "state.json",
        db_path=tmp_path / "kino.db",
    )


def _result(**overrides) -> RunResult:
    base = dict(
        caption="Подпись поста",
        image_url="https://example.org/pic.jpg",
        format="news",
        published=False,
        dry_run=True,
        kind="photo",
        link="https://example.org/a",
    )
    base.update(overrides)
    return RunResult(**base)


@pytest.fixture(autouse=True)
def _reset_state():
    """Замок — глобальный объект; между тестами он обязан быть чистым."""
    posting.STATE.end()
    yield
    posting.STATE.end()


# --- Prepared ----------------------------------------------------------------


def test_prepared_ok_when_caption_and_not_aborted():
    prepared = posting.Prepared(caption="текст", image_url="", kind="text", format="news")
    assert prepared.ok is True


def test_prepared_not_ok_when_caption_empty():
    prepared = posting.Prepared(caption="   ", image_url="", kind="text", format="news")
    assert prepared.ok is False


def test_prepared_not_ok_when_aborted():
    prepared = posting.Prepared(
        caption="текст", image_url="", kind="text", format="news",
        aborted=True, abort_reason="дедлайн",
    )
    assert prepared.ok is False


# --- build_only --------------------------------------------------------------


def test_build_only_uses_dry_run(tmp_path):
    """Тестовый пост обязан быть dry-run: иначе он съест новость из state."""
    cfg = _cfg(tmp_path)
    with patch("kino.posting.run", return_value=_result()) as mocked:
        prepared = posting.build_only(cfg)

    args, kwargs = mocked.call_args
    assert kwargs.get("dry_run") is True
    assert prepared.caption == "Подпись поста"
    assert prepared.kind == "photo"
    assert prepared.link == "https://example.org/a"


def test_build_only_carries_abort(tmp_path):
    cfg = _cfg(tmp_path)
    aborted = _result(aborted=True, abort_reason="бюджет исчерпан", caption="")
    with patch("kino.posting.run", return_value=aborted):
        prepared = posting.build_only(cfg)

    assert prepared.ok is False
    assert prepared.abort_reason == "бюджет исчерпан"


def test_build_only_defaults_kind_to_photo(tmp_path):
    """Пустой kind — это фото: без вариации пост всё равно должен уйти."""
    cfg = _cfg(tmp_path)
    with patch("kino.posting.run", return_value=_result(kind="")):
        prepared = posting.build_only(cfg)

    assert prepared.kind == "photo"


# --- deliver -----------------------------------------------------------------


def test_deliver_photo(tmp_path):
    cfg = _cfg(tmp_path)
    prepared = posting.Prepared(
        caption="подпись", image_url="https://example.org/p.jpg",
        kind="photo", format="news",
    )
    with patch("kino.publish.send_photo", return_value=True) as photo:
        assert posting.deliver(cfg, prepared, 42) is True

    photo.assert_called_once_with(cfg, "42", "https://example.org/p.jpg", "подпись")


def test_deliver_link(tmp_path):
    cfg = _cfg(tmp_path)
    prepared = posting.Prepared(
        caption="подпись", image_url="", kind="link", format="trailer",
        link="https://youtu.be/x",
    )
    with patch("kino.publish.send_link", return_value=True) as link:
        assert posting.deliver(cfg, prepared, 42) is True

    link.assert_called_once_with(cfg, "42", "подпись", "https://youtu.be/x")


def test_deliver_gallery_with_single_image_falls_back_to_photo(tmp_path):
    """Telegram не принимает галерею из одного фото — честнее отправить фото."""
    cfg = _cfg(tmp_path)
    prepared = posting.Prepared(
        caption="подпись", image_url="https://example.org/p.jpg",
        kind="gallery", format="collage",
    )
    with patch("kino.publish.send_photo", return_value=True) as photo, \
         patch("kino.publish.send_media_group") as group:
        assert posting.deliver(cfg, prepared, 42) is True

    photo.assert_called_once()
    group.assert_not_called()


def test_deliver_text_when_no_image(tmp_path):
    cfg = _cfg(tmp_path)
    prepared = posting.Prepared(
        caption="подпись", image_url="", kind="photo", format="facts",
    )
    with patch("kino.publish.send_text", return_value=True) as text:
        assert posting.deliver(cfg, prepared, 42) is True

    text.assert_called_once_with(cfg, "42", "подпись")


def test_deliver_refuses_empty_post(tmp_path):
    cfg = _cfg(tmp_path)
    prepared = posting.Prepared(caption="", image_url="", kind="text", format="news")
    with patch("kino.publish.send_text") as text:
        assert posting.deliver(cfg, prepared, 42) is False

    text.assert_not_called()


# --- publish_now -------------------------------------------------------------


def test_publish_now_is_not_dry_run(tmp_path):
    """Финальный пост идёт полным путём: state, дедуп, runs."""
    cfg = _cfg(tmp_path)
    with patch("kino.posting.run", return_value=_result()) as mocked:
        posting.publish_now(cfg)

    _, kwargs = mocked.call_args
    assert kwargs.get("dry_run") is False


# --- PostingState ------------------------------------------------------------


def test_begin_blocks_second_run():
    state = posting.PostingState()
    assert state.begin("test") is True
    assert state.begin("final") is False
    assert state.mode == "test"

    state.end()
    assert state.begin("final") is True


def test_busy_clears_stale_mark():
    """Поток упал, отметка осталась — через RUN_STALE_SECONDS её снимаем."""
    state = posting.PostingState()
    state.begin("test")
    state.started_at = time.time() - posting.RUN_STALE_SECONDS - 1

    assert state.busy() is False
    assert state.mode == ""


# --- preview_text / describe_result ------------------------------------------


def test_preview_text_mentions_where_and_variation():
    prepared = posting.Prepared(
        caption="подпись", image_url="", kind="photo", format="news",
        link="https://example.org/a",
    )
    text = posting.preview_text(prepared, to_channel=False)

    assert "личка" in text
    assert "photo" in text
    assert "news" in text
    assert "https://example.org/a" in text


def test_preview_text_reports_failure():
    prepared = posting.Prepared(
        caption="", image_url="", kind="", format="", aborted=True,
        abort_reason="дедлайн",
    )
    text = posting.preview_text(prepared, to_channel=False)

    assert "не собран" in text
    assert "дедлайн" in text


def test_describe_result_published():
    text = posting.describe_result(_result(published=True), to_channel=True)
    assert "ушёл" in text
    assert "канал" in text


def test_describe_result_rejected():
    text = posting.describe_result(_result(verdict="reject"), to_channel=True)
    assert "отбил" in text


def test_describe_result_aborted():
    text = posting.describe_result(
        _result(aborted=True, abort_reason="дедлайн"), to_channel=True
    )
    assert "прерван" in text
    assert "дедлайн" in text


def test_describe_result_not_published():
    text = posting.describe_result(_result(), to_channel=False)
    assert "не ушёл" in text