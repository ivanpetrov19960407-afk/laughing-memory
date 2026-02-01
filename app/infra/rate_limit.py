from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class _UserRateState:
    minute_hits: list[datetime] = field(default_factory=list)
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
            state.minute_hits = [hit for hit in state.minute_hits if hit.timestamp() > cutoff]
            if len(state.minute_hits) >= self._per_minute:
                return False, "Лимит запросов. Попробуйте позже."
            state.minute_hits.append(now)

        if self._per_day and self._per_day > 0:
            today = now.date()
            if state.day != today:
                state.day = today
                state.day_count = 0
            if state.day_count >= self._per_day:
                return False, "Лимит запросов. Попробуйте позже."
            state.day_count += 1

        return True, ""
