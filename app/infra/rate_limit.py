from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class _UserRateState:
    minute_hits: deque[float] = field(default_factory=deque)
    day: datetime.date | None = None
    day_count: int = 0


class RateLimiter:
    def __init__(self, per_minute: int | None, per_day: int | None) -> None:
        self._per_minute = per_minute
        self._per_day = per_day
        self._state: dict[int, _UserRateState] = defaultdict(_UserRateState)

    def check(self, user_id: int) -> tuple[bool, str]:
        now = datetime.now(timezone.utc)
        state = self._state[user_id]

        if self._per_minute and self._per_minute > 0:
            cutoff = now.timestamp() - 60
            while state.minute_hits and state.minute_hits[0] <= cutoff:
                state.minute_hits.popleft()
            if len(state.minute_hits) >= self._per_minute:
                return False, "Слишком часто, попробуйте позже."
            state.minute_hits.append(now.timestamp())

        if self._per_day and self._per_day > 0:
            today = now.date()
            if state.day != today:
                state.day = today
                state.day_count = 0
            if state.day_count >= self._per_day:
                return False, "Слишком часто, попробуйте позже."
            state.day_count += 1

        return True, ""
