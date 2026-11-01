"""Регрессии цикла serve: расписание, счётчик дня, остановка, once."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kino.config import Config, Settings, Source
from kino.serve import Stopper, build_schedule, serve


def _cfg(tmp_path: Path) -> Config:
    return Config(
        sources=[Source(name="test", url="http://example.com/feed")],
        weights={"facts": 1},
        bad=[],
        sad=[],
        settings=Settings(
            schedule_windows=["11:00-18:00", "22:50"],
            max_posts_per_day=3,
            min_gap_minutes=45,
            timezone="Europe/Moscow",
            tick_seconds=60,
            log_retention_days=14,
            log_max_total_bytes=1024 * 1024,
            log_purge_interval_hours=24,
            log_file=str(tmp_path / "logs" / "kino.log"),
        ),
        llm_base_url="http://llm.test",
        llm_api_key="k",
        draft_model="d",
        judge_model="j",
        bot_token="b",
        channel_chat_id="-100",
        admin_chat_id="",
        state_path=tmp_path / "state.json",
    )


def _result(published: bool = True, aborted: bool = False, verdict: str = "accept") -> SimpleNamespace:
    return SimpleNamespace(published=published, aborted=aborted, abort_reason="",
                           verdict=verdict, kind="photo", requested_kind="photo")


def test_stopper_install_and_request():
    stopper = Stopper()
    assert stopper.stopping is False
    stopper.request()
    assert stopper.stopping is True


def test_build_schedule_from_config(tmp_path):
    cfg = _cfg(tmp_path)
    schedule = build_schedule(cfg)
    assert [str(w) for w in schedule.windows] == ["11:00-18:00", "22:50"]
    assert schedule.max_posts_per_day == 3


def test_serve_publishes_and_increments_counter(tmp_path):
    """Внутри окна serve публикует и сохраняет счётчик дня."""
    cfg = _cfg(tmp_path)
    with patch("kino.serve.run", return_value=_result(published=True)) as mock_run, \
         patch("kino.serve.time.sleep") as mock_sleep, \
         patch("kino.serve.datetime") as mock_dt:

        # Задаём время внутри окна 11:00-18:00.
        from datetime import datetime, timezone
        now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

        class _DT:
            @staticmethod
            def now(tz=None):
                return now.astimezone(tz) if tz else now

        mock_dt.now = _DT.now

        code = serve(cfg, once=True)
        assert code == 0
        mock_run.assert_called_once()

        # Счётчик дня сохранён.
        state = __import__("kino.state", fromlist=["load_state"]).load_state(cfg.state_path)
        assert state.posts_today == 1
        assert state.day == "2026-10-18"


def test_serve_once_without_publish_does_not_increment(tmp_path):
    """Если run() не опубликовал (reject/abort), счётчик не растёт."""
    cfg = _cfg(tmp_path)
    with patch("kino.serve.run", return_value=_result(published=False, verdict="reject")) as mock_run, \
         patch("kino.serve.time.sleep") as mock_sleep, \
         patch("kino.serve.datetime") as mock_dt:

        from datetime import datetime, timezone
        now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

        class _DT:
            @staticmethod
            def now(tz=None):
                return now.astimezone(tz) if tz else now

        mock_dt.now = _DT.now

        code = serve(cfg, once=True)
        assert code == 0
        mock_run.assert_called_once()

        from kino.state import load_state
        state = load_state(cfg.state_path)
        assert state.posts_today == 0


def test_serve_aborted_run_does_not_increment(tmp_path):
    cfg = _cfg(tmp_path)
    with patch("kino.serve.run", return_value=_result(published=False, aborted=True)), \
         patch("kino.serve.time.sleep") as mock_sleep, \
         patch("kino.serve.datetime") as mock_dt:

        from datetime import datetime, timezone
        now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

        class _DT:
            @staticmethod
            def now(tz=None):
                return now.astimezone(tz) if tz else now

        mock_dt.now = _DT.now

        serve(cfg, once=True)
        from kino.state import load_state
        assert load_state(cfg.state_path).posts_today == 0


def test_serve_does_not_publish_outside_window(tmp_path):
    """Ночью serve не вызывает run(), просто спит."""
    cfg = _cfg(tmp_path)
    with patch("kino.serve.run") as mock_run, \
         patch("kino.serve.time.sleep") as mock_sleep:
        from datetime import datetime, timezone
        now = datetime(2026, 10, 18, 3, 0, tzinfo=timezone.utc)

        with patch("kino.serve.datetime") as mock_dt:
            class _DT:
                @staticmethod
                def now(tz=None):
                    return now.astimezone(tz) if tz else now
            mock_dt.now = _DT.now

            code = serve(cfg, once=True)
            assert code == 0
            mock_run.assert_not_called()


def test_serve_respects_daily_limit(tmp_path):
    """Третий пост в день исчерпывает лимит — serve больше не публикует."""
    cfg = _cfg(tmp_path)

    # Предварительно ставим счётчик дня на 3.
    from kino.state import State, save_state
    state = State(day="2026-10-18", posts_today=3)
    save_state(cfg.state_path, state, 120, 60)

    with patch("kino.serve.run") as mock_run, \
         patch("kino.serve.time.sleep") as mock_sleep:
        from datetime import datetime, timezone
        now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

        with patch("kino.serve.datetime") as mock_dt:
            class _DT:
                @staticmethod
                def now(tz=None):
                    return now.astimezone(tz) if tz else now
            mock_dt.now = _DT.now

            serve(cfg, once=True)
            mock_run.assert_not_called()


def test_serve_stopper_stops_loop(tmp_path):
    """stopper.stopping=True завершает цикл без публикаций."""
    cfg = _cfg(tmp_path)
    with patch("kino.serve.run") as mock_run, \
         patch("kino.serve.time.sleep") as mock_sleep:
        from datetime import datetime, timezone
        now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

        with patch("kino.serve.datetime") as mock_dt:
            class _DT:
                @staticmethod
                def now(tz=None):
                    return now.astimezone(tz) if tz else now
            mock_dt.now = _DT.now

            # Ставим stopping до запуска.
            import kino.serve as serve_mod
            original_install = serve_mod.Stopper.install
            def fake_install(self):
                self._stop = True
            serve_mod.Stopper.install = fake_install
            try:
                code = serve(cfg)
                assert code == 0
                mock_run.assert_not_called()
            finally:
                serve_mod.Stopper.install = original_install