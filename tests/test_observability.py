from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

from app.bot import handlers
from app.core.orchestrator import Orchestrator
from app.infra.request_context import RequestContext, log_event, safe_log_payload, start_request
from app.infra.storage import TaskStorage


class DummyMessage:
    def __init__(self, text: str = "hi", message_id: int = 1) -> None:
        self.text = text
        self.caption = None
        self.message_id = message_id
        self.sent: list[str] = []

    async def reply_text(self, text: str, reply_markup=None) -> None:
        _ = reply_markup
        self.sent.append(text)


class DummyUpdate:
    def __init__(self, text: str = "hi") -> None:
        self.effective_user = SimpleNamespace(id=123, username="tester")
        self.effective_chat = SimpleNamespace(id=456)
        self.message = DummyMessage(text=text)
        self.effective_message = self.message
        self.callback_query = None


class DummyContext:
    def __init__(self, orchestrator: Orchestrator) -> None:
        self.application = SimpleNamespace(
            bot_data={
                "orchestrator": orchestrator,
                "settings": SimpleNamespace(strict_no_pseudo_sources=False),
            }
        )
        self.chat_data: dict[str, object] = {}


def _make_request_context(env: str = "prod") -> RequestContext:
    return RequestContext(
        correlation_id="test-corr",
        user_id=1,
        chat_id=2,
        message_id=3,
        timezone=None,
        ts=datetime.now(timezone.utc),
        env=env,
    )


def test_safe_log_payload_redacts_prod() -> None:
    ctx = _make_request_context(env="prod")
    payload = {
        "text": "секретный текст",
        "api_key": "token-123",
        "nested": {"prompt": "hello"},
    }
    sanitized = safe_log_payload(ctx, payload)
    assert sanitized["api_key"] == "***"
    assert sanitized["text"]["text_len"] == len("секретный текст")
    assert "text_preview" not in sanitized["text"]
    assert sanitized["nested"]["prompt"]["text_len"] == len("hello")


def test_log_event_contains_correlation_id(caplog) -> None:
    logger = logging.getLogger("test.observability")
    caplog.set_level(logging.INFO, logger="test.observability")
    ctx = _make_request_context(env="dev")

    log_event(logger, ctx, component="router", event="route.selected", status="ok", intent="command")

    payload = json.loads(caplog.records[-1].message)
    assert payload["correlation_id"] == "test-corr"
    assert payload["event"] == "route.selected"


def test_trace_order_router_orchestrator_tool_handler(tmp_path) -> None:
    async def _run_flow() -> list[dict[str, object]]:
        storage = TaskStorage(tmp_path / "bot.db")
        orchestrator = Orchestrator(config={}, storage=storage)
        update = DummyUpdate(text="/task echo hello")
        context = DummyContext(orchestrator)
        request_context = start_request(update, context)
        await handlers._log_route(update, context, "task")
        result = orchestrator.execute_task(
            user_id=update.effective_user.id,
            task_name="echo",
            payload="hello",
            request_context=request_context,
        )
        await handlers.send_result(update, context, result)
        return request_context.trace

    trace = asyncio.run(_run_flow())
    components = [step["component"] for step in trace]
    assert components[:4] == ["router", "orchestrator", "tool", "handler"]
