"""Тесты инициализации AppScheduler и точки входа main() в контексте event loop (Python 3.12)."""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.app_scheduler import AppScheduler


def _build_settings(tmp_path: Path):
    """Минимальные Settings для теста main (совместимо с test_startup_checks)."""
    from app.infra.config import Settings

    config_path = tmp_path / "orchestrator.json"
    config_path.write_text("{}", encoding="utf-8")
    return Settings(
        bot_token="token",
        orchestrator_config_path=config_path,
        db_path=tmp_path / "bot.db",
        openai_api_key=None,
        openai_model="gpt-3.5-turbo",
        openai_timeout_seconds=30.0,
        perplexity_api_key=None,
        perplexity_base_url="https://api.perplexity.ai",
        perplexity_model="sonar",
        perplexity_timeout_seconds=15.0,
        allowed_user_ids=set(),
        admin_user_ids=set(),
        allowlist_path=tmp_path / "allowlist.json",
        llm_per_minute=None,
        llm_per_day=None,
        llm_history_turns=None,
        facts_only_default=None,
        rate_limit_per_minute=10,
        rate_limit_per_day=200,
        history_size=10,
        telegram_message_limit=4000,
        dialog_memory_path=tmp_path / "dialog_memory.json",
        context_max_turns=5,
        reminders_enabled=True,
        reminder_default_offset_minutes=10,
        reminder_max_future_days=365,
        action_ttl_seconds=900,
        action_max_size=2000,
        actions_log_ttl_days=30,
        enable_wizards=True,
        enable_menu=True,
        strict_no_pseudo_sources=True,
        wizard_store_path=tmp_path / "wizards",
        wizard_timeout_seconds=600,
        feature_web_search=True,
        uploads_path=tmp_path / "uploads",
        document_texts_path=tmp_path / "document_texts",
        document_sessions_path=tmp_path / "document_sessions.json",
        ocr_enabled=True,
        calendar_backend="local",
        caldav_url=None,
        caldav_username=None,
        caldav_password=None,
        caldav_calendar_name=None,
        obs_http_enabled=False,
        obs_http_host="127.0.0.1",
        obs_http_port=8080,
        otel_enabled=False,
        otel_exporter="console",
        otel_otlp_endpoint=None,
        systemd_watchdog_enabled=False,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+: get_event_loop() raises when no loop")
def test_app_scheduler_start_from_sync_raises_py312() -> None:
    """Вызов AppScheduler.start() из потока без event loop на 3.12 приводит к RuntimeError."""
    mock_app = MagicMock()
    mock_app.bot_data = {}
    scheduler = AppScheduler(application=mock_app)
    result: list[BaseException | None] = []

    def run_in_thread_without_loop() -> None:
        try:
            scheduler.start()
            result.append(None)
        except RuntimeError as e:
            result.append(e)

    t = threading.Thread(target=run_in_thread_without_loop)
    t.start()
    t.join()
    assert len(result) == 1, f"Expected one result, got {result}"
    assert isinstance(result[0], RuntimeError), f"Expected RuntimeError, got {result[0]}"
    assert "no current event loop" in str(result[0]) or "event loop" in str(result[0]).lower()


def test_app_scheduler_start_inside_async_succeeds() -> None:
    """AppScheduler.start() и shutdown() корректно работают при вызове из running event loop (3.11 и 3.12)."""

    async def run() -> None:
        mock_app = MagicMock()
        mock_app.bot_data = {}
        scheduler = AppScheduler(application=mock_app)
        scheduler.start()
        assert scheduler._scheduler.running
        scheduler.shutdown(wait=True)

    asyncio.run(run())


def test_main_scheduler_started_inside_event_loop(tmp_path: Path) -> None:
    """
    main() не вызывает app_scheduler.start() до появления running loop.
    Регрессия: на 3.12 раньше падало RuntimeError при вызове start() из main() до run_polling().
    """
    settings = _build_settings(tmp_path)
    (tmp_path / "wizards").mkdir(parents=True, exist_ok=True)

    def fake_run_polling(self) -> None:
        async def run() -> None:
            await self.initialize()
            if self.post_init:
                await self.post_init(self)
            await self.shutdown()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()

    with (
        patch("app.main.load_settings", return_value=settings),
        patch("telegram.ext.Application.run_polling", fake_run_polling),
        patch("telegram.ext.Application.initialize", new_callable=AsyncMock),
    ):
        from app.main import main

        try:
            main()
        except RuntimeError as e:
            if "no current event loop" in str(e) or "current event loop" in str(e).lower():
                pytest.fail(f"main() must not raise event loop error (scheduler must start inside loop): {e}")
            raise
