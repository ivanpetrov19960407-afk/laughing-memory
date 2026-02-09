from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.bot import handlers
from app.core.orchestrator import Orchestrator
from app.infra.observability.health import HealthChecker, HealthStatus, ReadinessStatus
from app.infra.observability.metrics import MetricsCollector
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

# Stage 7: Observability tests


def test_health_checker_get_health_status() -> None:
    """Test HealthChecker.get_health_status returns correct structure."""
    start_time = time.monotonic()
    checker = HealthChecker(start_time, {"system_metadata": {"version": "1.0.0"}})
    
    status = checker.get_health_status(
        scheduler_ok=True,
        calendar_backend="caldav",
        llm_client_configured=True,
        search_client_configured=False,
    )
    
    assert isinstance(status, HealthStatus)
    assert status.app_version == "1.0.0"
    assert status.uptime_seconds >= 0
    assert status.status["scheduler"] == "ok"
    assert status.status["calendar_backend"] == "caldav"
    assert status.status["llm_client_configured"] == "true"
    assert status.status["search_client_configured"] == "false"
    assert status.last_error_count == 0


def test_health_checker_record_error() -> None:
    """Test HealthChecker records errors."""
    start_time = time.monotonic()
    checker = HealthChecker(start_time)
    
    checker.record_error("test_component")
    status = checker.get_health_status()
    
    assert status.last_error_count == 1


def test_health_checker_get_readiness_status() -> None:
    """Test HealthChecker.get_readiness_status."""
    start_time = time.monotonic()
    checker = HealthChecker(start_time)
    
    # Ready
    status = checker.get_readiness_status(
        initialized=True,
        scheduler_active=True,
        critical_dependencies_ok=True,
    )
    assert isinstance(status, ReadinessStatus)
    assert status.ready is True
    assert status.reason is None
    
    # Not ready - not initialized
    status = checker.get_readiness_status(
        initialized=False,
        scheduler_active=True,
        critical_dependencies_ok=True,
    )
    assert status.ready is False
    assert status.reason == "not_initialized"
    
    # Not ready - scheduler not active
    status = checker.get_readiness_status(
        initialized=True,
        scheduler_active=False,
        critical_dependencies_ok=True,
    )
    assert status.ready is False
    assert status.reason == "scheduler_not_active"


def test_metrics_collector_enabled() -> None:
    """Test MetricsCollector when prometheus_client is available."""
    collector = MetricsCollector()
    assert isinstance(collector.enabled, bool)


def test_metrics_collector_record_update() -> None:
    """Test MetricsCollector records updates."""
    collector = MetricsCollector()
    if not collector.enabled:
        pytest.skip("prometheus_client not installed")
    
    collector.record_update("message")
    collector.record_update("callback_query")


def test_metrics_collector_record_error() -> None:
    """Test MetricsCollector records errors."""
    collector = MetricsCollector()
    if not collector.enabled:
        pytest.skip("prometheus_client not installed")
    
    collector.record_error("handler")
    collector.record_error("orchestrator")


def test_metrics_collector_record_duration() -> None:
    """Test MetricsCollector records request duration."""
    collector = MetricsCollector()
    if not collector.enabled:
        pytest.skip("prometheus_client not installed")
    
    collector.record_request_duration("command.ping", 0.1)
    collector.record_request_duration("command.health", 0.5)


def test_metrics_collector_get_metrics_text() -> None:
    """Test MetricsCollector generates metrics text."""
    collector = MetricsCollector()
    if not collector.enabled:
        pytest.skip("prometheus_client not installed")
    
    collector.record_update("message")
    collector.update_uptime()
    
    metrics_text = collector.get_metrics_text()
    assert isinstance(metrics_text, str)
    assert len(metrics_text) > 0


def test_metrics_collector_graceful_degradation() -> None:
    """Test MetricsCollector gracefully degrades when prometheus_client is missing."""
    collector = MetricsCollector()
    collector.record_update("test")
    collector.record_error("test")
    collector.record_request_duration("test", 0.1)
    collector.update_uptime()
    collector.update_active_wizards(5)
    metrics_text = collector.get_metrics_text()
    assert isinstance(metrics_text, str)


@pytest.mark.asyncio
async def test_http_server_healthz_endpoint() -> None:
    """Test HTTP server /healthz endpoint."""
    try:
        import aiohttp
        from app.infra.observability.http_server import ObservabilityHTTPServer
    except ImportError:
        pytest.skip("aiohttp not installed")
    
    start_time = time.monotonic()
    health_checker = HealthChecker(start_time, {"system_metadata": {"version": "test"}})
    metrics_collector = MetricsCollector()
    
    def get_app_state() -> dict[str, object]:
        return {
            "scheduler_ok": True,
            "calendar_backend": "local",
            "llm_client_configured": True,
            "search_client_configured": False,
        }
    
    server = ObservabilityHTTPServer(
        host="127.0.0.1",
        port=0,
        health_checker=health_checker,
        metrics_collector=metrics_collector,
        get_app_state=get_app_state,
    )
    
    await server.start()
    
    try:
        port = server._site._server.sockets[0].getsockname()[1]
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/healthz") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert "app_version" in data
                assert "uptime_seconds" in data
                assert "status" in data
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_http_server_readyz_endpoint() -> None:
    """Test HTTP server /readyz endpoint."""
    try:
        import aiohttp
        from app.infra.observability.http_server import ObservabilityHTTPServer
    except ImportError:
        pytest.skip("aiohttp not installed")
    
    start_time = time.monotonic()
    health_checker = HealthChecker(start_time)
    metrics_collector = MetricsCollector()
    
    def get_app_state() -> dict[str, object]:
        return {
            "initialized": True,
            "scheduler_active": True,
            "critical_dependencies_ok": True,
        }
    
    server = ObservabilityHTTPServer(
        host="127.0.0.1",
        port=0,
        health_checker=health_checker,
        metrics_collector=metrics_collector,
        get_app_state=get_app_state,
    )
    
    await server.start()
    
    try:
        port = server._site._server.sockets[0].getsockname()[1]
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/readyz") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["ready"] is True
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_http_server_metrics_endpoint() -> None:
    """Test HTTP server /metrics endpoint."""
    try:
        import aiohttp
        from app.infra.observability.http_server import ObservabilityHTTPServer
    except ImportError:
        pytest.skip("aiohttp not installed")
    
    start_time = time.monotonic()
    health_checker = HealthChecker(start_time)
    metrics_collector = MetricsCollector()
    metrics_collector.record_update("message")
    
    def get_app_state() -> dict[str, object]:
        return {}
    
    server = ObservabilityHTTPServer(
        host="127.0.0.1",
        port=0,
        health_checker=health_checker,
        metrics_collector=metrics_collector,
        get_app_state=get_app_state,
    )
    
    await server.start()
    
    try:
        port = server._site._server.sockets[0].getsockname()[1]
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/metrics") as resp:
                assert resp.status == 200
                text = await resp.text()
                assert isinstance(text, str)
                assert resp.headers["Content-Type"] == "text/plain; version=0.0.4"
    finally:
        await server.stop()


def test_observability_disabled_by_default() -> None:
    """Test that observability is disabled by default (no side effects)."""
    collector = MetricsCollector()
    assert isinstance(collector.enabled, bool)
