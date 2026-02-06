from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol


CalendarBackendName = Literal["local", "caldav"]


@dataclass(frozen=True)
class CalendarCreateResult:
    uid: str
    debug: dict[str, Any]


class CalendarBackend(Protocol):
    name: CalendarBackendName

    async def create_event(
        self,
        *,
        title: str,
        start_dt: datetime,
        end_dt: datetime,
        description: str | None = None,
        location: str | None = None,
        tz: str | None = None,
    ) -> CalendarCreateResult: ...

