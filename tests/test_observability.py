from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

from app.bot import handlers
from app.core.orchestrator import Orchestrator
from app.infra.request_context import RequestContext, log_error, log_event, safe_log_payload, start_request
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
    assert components[:5] == ["router", "tool", "orchestrator", "tool", "handler"]


def test_tool_call_logs_have_duration(tmp_path, caplog) -> None:
    logger_name = "app.core.orchestrator"
    caplog.set_level(logging.INFO, logger=logger_name)
    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(config={}, storage=storage)
    ctx = _make_request_context()
    orchestrator.execute_task(user_id=1, task_name="echo", payload="hello", request_context=ctx)

    records = [json.loads(record.message) for record in caplog.records if record.name == logger_name]
    start_event = next(entry for entry in records if entry["event"] == "tool.call.start")
    end_event = next(entry for entry in records if entry["event"] == "tool.call.end")

    assert start_event["correlation_id"] == end_event["correlation_id"]
    assert end_event["duration_ms"] > 0


def test_trace_steps_have_duration_for_tool_and_handler(tmp_path) -> None:
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
    tool_steps = [step for step in trace if step["component"] == "tool" and step["status"] != "start"]
    handler_steps = [step for step in trace if step["component"] == "handler"]
    assert tool_steps and handler_steps
    assert all(step["duration_ms"] is not None for step in tool_steps)
    assert all(step["duration_ms"] is not None for step in handler_steps)


def test_log_error_omits_stack_in_prod(caplog) -> None:
    logger = logging.getLogger("test.error")
    caplog.set_level(logging.ERROR, logger="test.error")
    ctx = _make_request_context(env="prod")

    try:
        raise ValueError("Sensitive error")
    except ValueError as exc:
        log_error(logger, ctx, component="handler", where="handler.send", exc=exc)

    payload = json.loads(caplog.records[-1].message)
    assert payload["event"] == "error"
    assert payload["component"] == "handler"
    assert payload["where"] == "handler.send"
    assert payload["exc_type"] == "ValueError"
    assert payload["correlation_id"] == "test-corr"
    assert "stack" not in payload


def test_llm_wrapper_logs_start_end(tmp_path, caplog) -> None:
    class DummyLLM:
        api_key = "test"

        async def generate_text(self, *, model: str, messages: list[dict[str, object]], **kwargs: object) -> str:
            _ = model
            _ = messages
            _ = kwargs
            return "Ответ"

    logger_name = "app.core.orchestrator"
    caplog.set_level(logging.INFO, logger=logger_name)
    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(config={}, storage=storage, llm_client=DummyLLM())
    ctx = _make_request_context()

    async def _run_llm() -> None:
        await orchestrator.ask_llm(user_id=1, prompt="Привет", request_context=ctx)

    asyncio.run(_run_llm())

    records = [json.loads(record.message) for record in caplog.records if record.name == logger_name]
    start_event = next(entry for entry in records if entry["event"] == "llm.call.start")
    end_event = next(entry for entry in records if entry["event"] == "llm.call.end")

    assert start_event["correlation_id"] == end_event["correlation_id"]
    assert end_event["duration_ms"] > 0
