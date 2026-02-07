from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable


@dataclass
class LastState:
    last_intent: str | None
    last_event_id: str | None
    last_reminder_id: str | None
    last_calendar_id: str | None
    last_query: str | None
    last_correlation_id: str | None
    updated_at: datetime


class LastStateStore:
    def __init__(
        self,
        *,
        ttl_seconds: int = 7 * 24 * 3600,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._ttl = timedelta(seconds=max(1, ttl_seconds))
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._store: dict[tuple[int, int], LastState] = {}

    def get_state(self, *, chat_id: int, user_id: int) -> LastState | None:
        key = (chat_id, user_id)
        self._cleanup(key)
        return self._store.get(key)

    def update_state(
        self,
        *,
        chat_id: int,
        user_id: int,
        intent: str | None,
        correlation_id: str | None,
        event_id: str | None = None,
        reminder_id: str | None = None,
        calendar_id: str | None = None,
        query: str | None = None,
    ) -> LastState:
        key = (chat_id, user_id)
        now = self._now_provider()
        current = self._store.get(key)
        state = LastState(
            last_intent=intent if isinstance(intent, str) and intent.strip() else current.last_intent if current else None,
            last_event_id=event_id if isinstance(event_id, str) and event_id.strip() else current.last_event_id if current else None,
            last_reminder_id=reminder_id if isinstance(reminder_id, str) and reminder_id.strip() else current.last_reminder_id if current else None,
            last_calendar_id=calendar_id if isinstance(calendar_id, str) and calendar_id.strip() else current.last_calendar_id if current else None,
            last_query=query if isinstance(query, str) and query.strip() else current.last_query if current else None,
            last_correlation_id=correlation_id
            if isinstance(correlation_id, str) and correlation_id.strip()
            else current.last_correlation_id
            if current
            else None,
            updated_at=now,
        )
        self._store[key] = state
        return state

    def _cleanup(self, key: tuple[int, int]) -> None:
        state = self._store.get(key)
        if not state:
            return
        now = self._now_provider()
        if now - state.updated_at > self._ttl:
            self._store.pop(key, None)
