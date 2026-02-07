from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

import httpx

from app.infra.request_context import RequestContext, log_event

LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class TimeoutConfig:
    tool_call_seconds: float = 15.0
    web_tool_call_seconds: float = 20.0
    llm_seconds: float = 20.0
    external_api_seconds: float = 10.0


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_ms: int = 250
    max_delay_ms: int = 2000
    jitter_ms: int = 200


@dataclass(frozen=True)
class CircuitBreakerConfig:
    failure_threshold: int = 5
    window_seconds: float = 60.0
    cooldown_seconds: float = 60.0


def load_timeouts(config: dict[str, Any]) -> TimeoutConfig:
    raw = config.get("timeouts", {}) if isinstance(config, dict) else {}
    return TimeoutConfig(
        tool_call_seconds=float(raw.get("tool_call_seconds", 15.0)),
        web_tool_call_seconds=float(raw.get("web_tool_call_seconds", 20.0)),
        llm_seconds=float(raw.get("llm_seconds", 20.0)),
        external_api_seconds=float(raw.get("external_api_seconds", 10.0)),
    )


def load_retry_policy(config: dict[str, Any]) -> RetryPolicy:
    raw = config.get("retry", {}) if isinstance(config, dict) else {}
    return RetryPolicy(
        max_attempts=int(raw.get("max_attempts", 3)),
        base_delay_ms=int(raw.get("base_delay_ms", 250)),
        max_delay_ms=int(raw.get("max_delay_ms", 2000)),
        jitter_ms=int(raw.get("jitter_ms", 200)),
    )


def load_circuit_breaker_config(config: dict[str, Any]) -> CircuitBreakerConfig:
    raw = config.get("circuit_breaker", {}) if isinstance(config, dict) else {}
    return CircuitBreakerConfig(
        failure_threshold=int(raw.get("failure_threshold", 5)),
        window_seconds=float(raw.get("window_seconds", 60.0)),
        cooldown_seconds=float(raw.get("cooldown_seconds", 60.0)),
    )


def _next_backoff_ms(policy: RetryPolicy, attempt: int) -> int:
    exp = min(policy.max_delay_ms, int(policy.base_delay_ms * (2 ** max(attempt - 1, 0))))
    jitter = int(random.random() * policy.jitter_ms) if policy.jitter_ms > 0 else 0
    return min(policy.max_delay_ms, exp + jitter)


def is_timeout_error(exc: Exception) -> bool:
    return isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException))


def is_network_error(exc: Exception) -> bool:
    return isinstance(exc, httpx.TransportError)


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    timeout_seconds: float | None,
    logger: logging.Logger,
    request_context: RequestContext | None,
    component: str,
    name: str,
    is_retryable: Callable[[Exception], bool],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    attempts = max(1, policy.max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            if timeout_seconds and timeout_seconds > 0:
                return await asyncio.wait_for(func(), timeout=timeout_seconds)
            return await func()
        except Exception as exc:
            if attempt >= attempts or not is_retryable(exc):
                raise
            wait_ms = _next_backoff_ms(policy, attempt)
            log_event(
                logger,
                request_context,
                component=component,
                event="retry.attempt",
                status="ok",
                name=name,
                attempt=attempt + 1,
                wait_ms=wait_ms,
            )
            await sleep(wait_ms / 1000)
    raise RuntimeError("retry_attempts_exhausted")


def retry_sync(
    func: Callable[[], T],
    *,
    policy: RetryPolicy,
    logger: logging.Logger,
    request_context: RequestContext | None,
    component: str,
    name: str,
    is_retryable: Callable[[Exception], bool],
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    attempts = max(1, policy.max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:
            if attempt >= attempts or not is_retryable(exc):
                raise
            wait_ms = _next_backoff_ms(policy, attempt)
            log_event(
                logger,
                request_context,
                component=component,
                event="retry.attempt",
                status="ok",
                name=name,
                attempt=attempt + 1,
                wait_ms=wait_ms,
            )
            sleep(wait_ms / 1000)
    raise RuntimeError("retry_attempts_exhausted")


class CircuitBreaker:
    def __init__(
        self,
        *,
        name: str,
        config: CircuitBreakerConfig,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._name = name
        self._config = config
        self._time_fn = time_fn
        self._state = "closed"
        self._opened_at: float | None = None
        self._half_open_in_flight = False
        self._failures: list[float] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> str:
        return self._state

    def allow_request(self) -> tuple[bool, str | None]:
        now = self._time_fn()
        if self._state == "open":
            opened_at = self._opened_at if self._opened_at is not None else now
            if now - opened_at < self._config.cooldown_seconds:
                return False, None
            self._state = "half_open"
            self._half_open_in_flight = False
            return True, "circuit.half_open"
        if self._state == "half_open":
            if self._half_open_in_flight:
                return False, None
            self._half_open_in_flight = True
            return True, None
        return True, None

    def record_success(self) -> str | None:
        if self._state == "half_open":
            self._state = "closed"
            self._half_open_in_flight = False
            self._failures.clear()
            self._opened_at = None
            return "circuit.closed"
        return None

    def record_failure(self) -> str | None:
        now = self._time_fn()
        if self._state == "half_open":
            self._state = "open"
            self._opened_at = now
            self._half_open_in_flight = False
            self._failures.clear()
            return "circuit.open"
        self._failures.append(now)
        self._prune_failures(now)
        if len(self._failures) >= self._config.failure_threshold:
            self._state = "open"
            self._opened_at = now
            self._half_open_in_flight = False
            self._failures.clear()
            return "circuit.open"
        return None

    def _prune_failures(self, now: float) -> None:
        window = self._config.window_seconds
        if window <= 0:
            self._failures.clear()
            return
        cutoff = now - window
        self._failures = [ts for ts in self._failures if ts >= cutoff]


class CircuitBreakerRegistry:
    def __init__(
        self,
        *,
        config: CircuitBreakerConfig,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._time_fn = time_fn
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, name: str) -> CircuitBreaker:
        breaker = self._breakers.get(name)
        if breaker is None:
            breaker = CircuitBreaker(name=name, config=self._config, time_fn=self._time_fn)
            self._breakers[name] = breaker
        return breaker
