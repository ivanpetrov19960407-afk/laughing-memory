from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.bot import handlers
from app.core.memory_store import MemoryStore
from app.infra.rate_limiter import RateLimiter
from app.infra.trace_store import TraceStore


def asyncio_run(coro):
    return asyncio.run(coro)


def _build_update(text: str = "/health") -> SimpleNamespace:
    message = SimpleNamespace(text=text, caption=None, message_id=1)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=1, username="tester"),
        effective_chat=SimpleNamespace(id=10, type="private"),
        message=message,
        effective_message=message,
        callback_query=None,
    )


def _build_context(settings) -> SimpleNamespace:
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "settings": settings,
                "admin_user_ids": {1},
                "rate_limiter": RateLimiter(),
                "ui_rate_limiter": RateLimiter(),
                "orchestrator": SimpleNamespace(
                    config={"system_metadata": {"version": "test-version"}},
                    is_facts_only=lambda user_id: False,
                ),
                "memory_store": MemoryStore(),
                "trace_store": TraceStore(max_items=5, ttl_seconds=60),
            }
        ),
        chat_data={},
    )


@pytest.fixture
def calendar_env(tmp_path, monkeypatch) -> str:
    path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(path))
    return str(path)


def test_health_command_returns_status(monkeypatch, calendar_env) -> None:
    _ = calendar_env
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        _ = update
        _ = context
        _ = reply_markup
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        _ = update
        _ = context
        _ = bucket
        return True

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    settings = SimpleNamespace(
        orchestrator_config_path="config/orchestrator.json",
        reminders_enabled=True,
        caldav_url=None,
        caldav_username=None,
        caldav_password=None,
        openai_api_key="",
        perplexity_api_key=None,
        calendar_backend="local",
        enable_wizards=True,
        enable_menu=True,
        strict_no_pseudo_sources=True,
        feature_web_search=False,
        db_path="data/bot.db",
        allowlist_path="data/allowlist.json",
        dialog_memory_path="data/dialog_memory.json",
        wizard_store_path="data/wizards",
    )
    update = _build_update("/health")
    context = _build_context(settings)

    asyncio_run(handlers.health(update, context))

    result = captured["result"]
    assert result.status == "ok"
    assert "vtest-version" in result.text
    assert "env dev" in result.text


def test_config_command_dev_only(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        _ = update
        _ = context
        _ = reply_markup
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        _ = update
        _ = context
        _ = bucket
        return True

    settings = SimpleNamespace(
        orchestrator_config_path="config/orchestrator.json",
        reminders_enabled=True,
        caldav_url=None,
        caldav_username=None,
        caldav_password=None,
        openai_api_key=None,
        perplexity_api_key=None,
        calendar_backend="local",
        enable_wizards=True,
        enable_menu=True,
        strict_no_pseudo_sources=True,
        feature_web_search=False,
        db_path="data/bot.db",
        allowlist_path="data/allowlist.json",
        dialog_memory_path="data/dialog_memory.json",
        wizard_store_path="data/wizards",
    )
    update = _build_update("/config")
    context = _build_context(settings)

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    asyncio_run(handlers.config_command(update, context))
    result = captured["result"]
    assert result.status == "ok"
    assert "Config:" in result.text

    monkeypatch.setenv("APP_ENV", "prod")
    asyncio_run(handlers.config_command(update, context))
    refused = captured["result"]
    assert refused.status == "refused"
