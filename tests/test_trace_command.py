from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from app.bot import actions, handlers
from app.core.result import Action, ok
from app.infra.rate_limiter import RateLimiter
from app.infra.request_context import start_request
from app.infra.trace_store import TraceEntry, TraceStore


class DummyOrchestrator:
    def __init__(self) -> None:
        self.config = {"system_metadata": {"version": "test"}}

    def is_facts_only(self, user_id: int) -> bool:
        return False

    def is_access_restricted(self) -> bool:
        return False


class DummyContext:
    def __init__(self, trace_store: TraceStore | None = None) -> None:
        self.application = SimpleNamespace(
            bot_data={
                "orchestrator": DummyOrchestrator(),
                "action_store": actions.ActionStore(),
                "rate_limiter": RateLimiter(),
                "ui_rate_limiter": RateLimiter(),
                "settings": SimpleNamespace(enable_menu=True, enable_wizards=False, strict_no_pseudo_sources=False),
                "trace_store": trace_store or TraceStore(max_items=20, ttl_seconds=86400),
            }
        )
        self.chat_data: dict[str, object] = {}
        self.args: list[str] = []


class DummyUpdate:
    def __init__(self, text: str = "/ping", user_id: int = 1, chat_id: int = 10, chat_type: str = "private") -> None:
        self.effective_user = SimpleNamespace(id=user_id, username="tester")
        self.effective_chat = SimpleNamespace(id=chat_id, type=chat_type)
        message = SimpleNamespace(text=text, caption=None)
        self.message = message
        self.effective_message = message
        self.callback_query = None


def _build_entry(correlation_id: str) -> TraceEntry:
    return TraceEntry(
        correlation_id=correlation_id,
        ts=datetime.now(timezone.utc),
        intent="command.ping",
        mode="local",
        status="ok",
        total_duration_ms=12.0,
        trace_steps=[{"step": "ping", "component": "handler", "name": "ping", "status": "ok", "duration_ms": 1.2}],
        durations={"total_ms": 12.0},
        tool_names=[],
        llm_models=[],
        tool_calls=[],
        llm_calls=[],
        error=None,
    )


def test_trace_command_lists_entries(monkeypatch) -> None:
    sent_texts: list[str] = []

    async def fake_send_text(update, context, text, reply_markup=None):
        sent_texts.append(text or "")
        return len(text or "")

    async def fake_guard_access(update, context, bucket="default"):
        return True

    monkeypatch.setattr(handlers, "safe_send_text", fake_send_text)
    monkeypatch.setattr(handlers, "safe_edit_text", fake_send_text)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    context = DummyContext()
    update = DummyUpdate(text="/ping")
    asyncio.run(handlers.ping(update, context))

    trace_store = context.application.bot_data["trace_store"]
    assert trace_store.list_entries(chat_id=10, user_id=1, limit=5)

    update.message.text = "/trace"
    update.effective_message.text = "/trace"
    context.args = []
    asyncio.run(handlers.trace_command(update, context))
    assert "Последние запросы" in sent_texts[-1]


def test_trace_dev_action_and_callback(monkeypatch) -> None:
    sent_actions: list[list[Action]] = []

    async def fake_send_text(update, context, text, reply_markup=None):
        return len(text or "")

    def fake_build_inline_keyboard(action_list, store, user_id, chat_id):
        sent_actions.append(action_list)
        return None

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setattr(handlers, "safe_send_text", fake_send_text)
    monkeypatch.setattr(handlers, "safe_edit_text", fake_send_text)
    monkeypatch.setattr(handlers, "build_inline_keyboard", fake_build_inline_keyboard)

    context = DummyContext()
    update = DummyUpdate(text="/ping")
    start_request(update, context)

    asyncio.run(handlers.send_result(update, context, ok("hi", intent="test", mode="local")))
    assert any(action.id == "debug.trace" for action in sent_actions[-1])

    trace_store = context.application.bot_data["trace_store"]
    trace_store.add_entry(chat_id=10, user_id=1, entry=_build_entry("trace-123"))
    result = asyncio.run(
        handlers._dispatch_action_payload(
            update,
            context,
            op="trace_last",
            payload={"op": "trace_last"},
            intent="debug.trace",
        )
    )
    assert "Steps:" in result.text


def test_trace_action_not_in_prod(monkeypatch) -> None:
    sent_actions: list[list[Action]] = []

    async def fake_send_text(update, context, text, reply_markup=None):
        return len(text or "")

    def fake_build_inline_keyboard(action_list, store, user_id, chat_id):
        sent_actions.append(action_list)
        return None

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr(handlers, "safe_send_text", fake_send_text)
    monkeypatch.setattr(handlers, "safe_edit_text", fake_send_text)
    monkeypatch.setattr(handlers, "build_inline_keyboard", fake_build_inline_keyboard)

    context = DummyContext()
    update = DummyUpdate(text="/ping")
    start_request(update, context)

    asyncio.run(handlers.send_result(update, context, ok("hi", intent="test", mode="local")))
    assert not any(action.id == "debug.trace" for action in sent_actions[-1])
