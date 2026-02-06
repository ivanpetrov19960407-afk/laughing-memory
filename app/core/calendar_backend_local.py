from __future__ import annotations

import uuid
from datetime import datetime

from app.core import calendar_store
from app.core.calendar_backend import CalendarBackend, CalendarCreateResult


class LocalCalendarBackend(CalendarBackend):
    name = "local"

    async def create_event(
        self,
        *,
        title: str,
        start_dt: datetime,
        end_dt: datetime,
        description: str | None = None,
        location: str | None = None,
        tz: str | None = None,
    ) -> CalendarCreateResult:
        # Local backend is an in-app fallback. We only generate a UID here.
        # Persisting to calendar store is done by CalendarTool to keep behavior consistent
        # across backends.
        if start_dt.tzinfo is None or end_dt.tzinfo is None:
            raise ValueError("naive_datetime")
        if end_dt <= start_dt:
            raise ValueError("invalid_event_range")
        uid = uuid.uuid4().hex[:8]
        return CalendarCreateResult(
            uid=uid,
            debug={
                "calendar_backend": "local",
                "tz": tz or str(calendar_store.BOT_TZ),
            },
        )

