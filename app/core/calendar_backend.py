from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core import calendar_store, tools_calendar_caldav

LOGGER = logging.getLogger(__name__)
BOT_TZ = ZoneInfo("Europe/Vilnius")


@dataclass(frozen=True)
class CalendarCreateResult:
    uid: str
    backend: str
    calendar_name: str | None = None
    url_base: str | None = None


@dataclass(frozen=True)
class CalendarEventItem:
    uid: str
    summary: str
    start_at: datetime


class CalendarBackend(ABC):
    """Abstract interface for calendar backends."""

    @abstractmethod
    async def create_event(
        self,
        *,
        title: str,
        start_dt: datetime,
        end_dt: datetime | None = None,
        description: str | None = None,
        location: str | None = None,
        tz: ZoneInfo | None = None,
    ) -> CalendarCreateResult:
        """Create a calendar event."""
        pass

    @abstractmethod
    async def list_events(
        self,
        *,
        start: datetime,
        end: datetime,
        limit: int = 20,
    ) -> list[CalendarEventItem]:
        """List calendar events in a time range."""
        pass

    @abstractmethod
    async def delete_event(self, *, event_id: str) -> bool:
        """Delete a calendar event by ID."""
        pass


class LocalCalendarBackend(CalendarBackend):
    """Local JSON-based calendar backend."""

    def __init__(self, user_id: int, chat_id: int):
        self.user_id = user_id
        self.chat_id = chat_id

    async def create_event(
        self,
        *,
        title: str,
        start_dt: datetime,
        end_dt: datetime | None = None,
        description: str | None = None,
        location: str | None = None,
        tz: ZoneInfo | None = None,
    ) -> CalendarCreateResult:
        """Create event in local calendar store."""
        tz = tz or BOT_TZ
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=tz)

        uid = str(uuid.uuid4())
        await calendar_store.add_item(
            dt=start_dt,
            title=title,
            chat_id=self.chat_id,
            remind_at=None,
            user_id=self.user_id,
            reminders_enabled=False,
            event_id=uid,
        )

        return CalendarCreateResult(
            uid=uid,
            backend="local",
        )

    async def list_events(
        self,
        *,
        start: datetime,
        end: datetime,
        limit: int = 20,
    ) -> list[CalendarEventItem]:
        """List events from local calendar store."""
        items = await calendar_store.list_items(start=start, end=end)
        result = []
        for item in items[:limit]:
            result.append(
                CalendarEventItem(
                    uid=item.id,
                    summary=item.title,
                    start_at=item.dt,
                )
            )
        return result

    async def delete_event(self, *, event_id: str) -> bool:
        """Delete event from local calendar store."""
        removed, _ = await calendar_store.delete_item(event_id)
        return removed


class CalDAVCalendarBackend(CalendarBackend):
    """CalDAV-based calendar backend."""

    def __init__(self, config: tools_calendar_caldav.CalDAVConfig):
        self.config = config

    async def create_event(
        self,
        *,
        title: str,
        start_dt: datetime,
        end_dt: datetime | None = None,
        description: str | None = None,
        location: str | None = None,
        tz: ZoneInfo | None = None,
    ) -> CalendarCreateResult:
        """Create event via CalDAV."""
        tz = tz or BOT_TZ
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=tz)

        created = await tools_calendar_caldav.create_event(
            self.config,
            start_at=start_dt,
            title=title,
            description=description,
        )

        calendar_name = self.config.calendar_name or "default"
        url_base = _sanitize_url(self.config.url)

        return CalendarCreateResult(
            uid=created.uid,
            backend="caldav",
            calendar_name=calendar_name,
            url_base=url_base,
        )

    async def list_events(
        self,
        *,
        start: datetime,
        end: datetime,
        limit: int = 20,
    ) -> list[CalendarEventItem]:
        """List events via CalDAV."""
        events = await tools_calendar_caldav.list_events(
            self.config,
            start=start,
            end=end,
            limit=limit,
        )
        result = []
        for event in events:
            result.append(
                CalendarEventItem(
                    uid=event.uid,
                    summary=event.summary,
                    start_at=event.start_at,
                )
            )
        return result

    async def delete_event(self, *, event_id: str) -> bool:
        """Delete event via CalDAV."""
        return await tools_calendar_caldav.delete_event(self.config, event_id=event_id)


def _sanitize_url(url: str) -> str:
    """Remove credentials from URL for debug output."""
    if not url:
        return ""
    if "@" in url:
        parts = url.split("@", 1)
        if len(parts) == 2:
            protocol_user = parts[0]
            host_path = parts[1]
            if "://" in protocol_user:
                protocol = protocol_user.split("://")[0]
                return f"{protocol}://***@{host_path}"
    return url
