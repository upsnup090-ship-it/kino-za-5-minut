"""Точечное уведомление админу о низком качестве.

_notify_low_quality срабатывает только когда судья реально оценил пост
ниже порога (5/10). Раньше overall_quality и recommendations считались и
выбрасывались; теперь низкая оценка доходит до админа одним сообщением.
"""

from __future__ import annotations

from unittest.mock import patch

from kino.choose import Choice
from kino.collect import FeedItem
from kino.config import Config, Settings
from kino.pipeline import _notify_low_quality


def _cfg() -> Config:
    return Config(
        settings=Settings(),
        bot_token="b",
        channel_chat_id="-100123",
        admin_chat_id="-100456",
    )


def _choice() -> Choice:
    item = FeedItem(
        source="Deadline", title="Трейлер Дюны", link="https://e.com/1",
        pub_date="", ts=0.0, snippet="d",
    )
    return Choice(item=item, format="news", quality_score=3, sad_topic=False)


def _final_data(score, recommendations=None) -> dict:
    return {
        "verdict": "accept",
        "analysis": {
            "judge_feedback": {"quality_score": score},
            "recommendations": recommendations or [],
        },
    }


def test_notifies_on_low_score():
    with patch("kino.publish.notify_admin") as mock:
        _notify_low_quality(
            _cfg(), _choice(),
            _final_data(3, ["Текст слишком короткий"]),
            dry_run=False,
        )
    mock.assert_called_once()
    msg = mock.call_args[0][1]
    assert "3/10" in msg
    assert "Текст слишком короткий" in msg


def test_does_not_notify_on_good_score():
    with patch("kino.publish.notify_admin") as mock:
        _notify_low_quality(_cfg(), _choice(), _final_data(8), dry_run=False)
    mock.assert_not_called()


def test_does_not_notify_at_threshold():
    """5/10 — граница: не ниже порога, уведомления нет."""
    with patch("kino.publish.notify_admin") as mock:
        _notify_low_quality(_cfg(), _choice(), _final_data(5), dry_run=False)
    mock.assert_not_called()


def test_does_not_notify_without_score():
    """Нет оценки (fallback/обрезка) — не шлём, иначе спам на каждый сбой."""
    with patch("kino.publish.notify_admin") as mock:
        _notify_low_quality(
            _cfg(), _choice(), _final_data(None), dry_run=False
        )
    mock.assert_not_called()


def test_does_not_notify_in_dry_run():
    with patch("kino.publish.notify_admin") as mock:
        _notify_low_quality(
            _cfg(), _choice(), _final_data(3, ["короткий"]), dry_run=True
        )
    mock.assert_not_called()


def test_notify_uses_first_recommendation_only():
    with patch("kino.publish.notify_admin") as mock:
        _notify_low_quality(
            _cfg(), _choice(),
            _final_data(2, ["Первая", "Вторая", "Третья"]),
            dry_run=False,
        )
    msg = mock.call_args[0][1]
    assert "Первая" in msg
    assert "Вторая" not in msg


def test_notify_handles_no_recommendations():
    with patch("kino.publish.notify_admin") as mock:
        _notify_low_quality(_cfg(), _choice(), _final_data(2), dry_run=False)
    mock.assert_called_once()
    assert "рекомендаций нет" in mock.call_args[0][1]


def test_notify_swallows_delivery_error():
    """Сбой доставки не должен ронять прогон."""
    with patch("kino.publish.notify_admin", side_effect=RuntimeError("net")):
        _notify_low_quality(
            _cfg(), _choice(), _final_data(2, ["x"]), dry_run=False
        )  # не должно бросить