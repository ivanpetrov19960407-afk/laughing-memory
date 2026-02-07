import asyncio
import logging
from datetime import datetime, timezone

import pytest

from app.core.error_messages import map_error_text
from app.core.orchestrator import Orchestrator
from app.infra.request_context import RequestContext
from app.infra.resilience import CircuitBreaker, CircuitBreakerConfig, RetryPolicy, retry_async
from app.infra.storage import TaskStorage


class SlowLLMClient:
    api_key = "slow-key"

    async def create_chat_completion(self, *, model, messages, max_tokens=None, web_search_options=None):
        return {"content": "slow"}

    async def generate_text(self, *, model, messages, max_tokens=None, web_search_options=None) -> str:
        await asyncio.sleep(0.05)
        return "slow"


def test_retry_success_after_transient() -> None:
    attempts: list[int] = []
    waits: list[float] = []

    async def _call() -> str:
        attempts.append(len(attempts))
        if len(attempts) < 3:
            raise asyncio.TimeoutError("transient")
        return "ok"

    async def _sleep(delay: float) -> None:
        waits.append(delay)

    policy = RetryPolicy(max_attempts=3, base_delay_ms=1, max_delay_ms=1, jitter_ms=0)

    result = asyncio.run(
        retry_async(
            _call,
            policy=policy,
            timeout_seconds=None,
            logger=logging.getLogger(__name__),
            request_context=None,
            component="test",
            name="retry",
            is_retryable=lambda exc: True,
            sleep=_sleep,
        )
    )

    assert result == "ok"
    assert len(attempts) == 3
    assert waits == [0.001, 0.001]


def test_retry_non_retryable_error() -> None:
    attempts: list[int] = []

    async def _call() -> str:
        attempts.append(len(attempts))
        raise ValueError("nope")

    policy = RetryPolicy(max_attempts=3, base_delay_ms=1, max_delay_ms=1, jitter_ms=0)

    with pytest.raises(ValueError):
        asyncio.run(
            retry_async(
                _call,
                policy=policy,
                timeout_seconds=None,
                logger=logging.getLogger(__name__),
                request_context=None,
                component="test",
                name="retry",
                is_retryable=lambda exc: False,
            )
        )

    assert len(attempts) == 1


def test_circuit_breaker_transitions() -> None:
    now = 0.0

    def _time() -> float:
        return now

    breaker = CircuitBreaker(
        name="svc",
        config=CircuitBreakerConfig(failure_threshold=2, window_seconds=60.0, cooldown_seconds=30.0),
        time_fn=_time,
    )

    allowed, _ = breaker.allow_request()
    assert allowed is True
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "open"

    allowed, _ = breaker.allow_request()
    assert allowed is False

    now = 31.0
    allowed, event = breaker.allow_request()
    assert allowed is True
    assert event == "circuit.half_open"
    assert breaker.state == "half_open"

    event = breaker.record_success()
    assert event == "circuit.closed"
    assert breaker.state == "closed"


def test_llm_timeout_records_trace_and_error(caplog, tmp_path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    config = {"timeouts": {"llm_seconds": 0.01}}
    orchestrator = Orchestrator(config=config, storage=storage, llm_client=SlowLLMClient())
    request_context = RequestContext(
        correlation_id="test-corr",
        user_id=1,
        chat_id=1,
        message_id=1,
        timezone=None,
        ts=datetime.now(timezone.utc),
        env="prod",
    )

    with caplog.at_level(logging.ERROR):
        result = asyncio.run(
            orchestrator.handle(
                "Что нового?",
                {"user_id": 1, "request_context": request_context},
            )
        )

    assert result.status == "error"
    assert result.text == map_error_text("timeout")
    assert any(step["step"] == "llm.call" and step["status"] == "error" for step in request_context.trace)
    assert "llm.timeout" in caplog.text
