from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class _UserRateState:
    minute_hits: deque[float] = field(default_factory=deque)
    day_start: float | None = None
    day_count: int = 0


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: float | None
    scope: str | None


class RateLimiter:
    def __init__(
        self,
        per_minute: int | None = None,
        per_day: int | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._per_minute = per_minute if per_minute is not None else 10
        self._per_day = per_day if per_day is not None else 200
        self._state: dict[int, _UserRateState] = defaultdict(_UserRateState)
        self._lock = asyncio.Lock()
        self._clock = clock or time.time

    @property
    def per_minute(self) -> int:
        return self._per_minute

    @property
    def per_day(self) -> int:
        return self._per_day

    @property
    def cache_size(self) -> int:
        return len(self._state)

    async def check(self, user_id: int) -> RateLimitResult:
        async with self._lock:
            now = self._clock()
            state = self._state[user_id]
            if self._per_minute and self._per_minute > 0:
                cutoff = now - 60
                while state.minute_hits and state.minute_hits[0] <= cutoff:
                    state.minute_hits.popleft()
                if len(state.minute_hits) >= self._per_minute:
                    retry_after = max(0.0, 60 - (now - state.minute_hits[0]))
                    return RateLimitResult(False, retry_after, "minute")
                state.minute_hits.append(now)
            if self._per_day and self._per_day > 0:
                if state.day_start is None or now - state.day_start >= 86400:
                    state.day_start = now
                    state.day_count = 0
                if state.day_count >= self._per_day:
                    retry_after = max(0.0, 86400 - (now - state.day_start))
                    return RateLimitResult(False, retry_after, "day")
                state.day_count += 1
            return RateLimitResult(True, None, None)


def _selftest() -> None:
    class _FakeClock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            return self.value

        def advance(self, seconds: float) -> None:
            self.value += seconds

    clock = _FakeClock()
    limiter = RateLimiter(per_minute=2, per_day=3, clock=clock)

    async def _run() -> None:
        result = await limiter.check(1)
        assert result.allowed
        result = await limiter.check(1)
        assert result.allowed
        result = await limiter.check(1)
        assert not result.allowed and result.scope == "minute"
        clock.advance(61)
        result = await limiter.check(1)
        assert result.allowed
        result = await limiter.check(1)
        assert not result.allowed and result.scope == "day"

    asyncio.run(_run())
    print("rate_limiter selftest passed")


if __name__ == "__main__":
    _selftest()
