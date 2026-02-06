from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from app.core import calendar_store, tools_calendar_caldav


DEFAULT_TZ = calendar_store.BOT_TZ


@dataclass(frozen=True)
class CalendarCreateResult:
    event_id: str
    debug: dict[str, str] = field(default_factory=dict)


class CalendarBackend(Protocol):
    async def create_event(
        self,
        title: str,
        start_dt: datetime,
        end_dt: datetime,
        description: str | None = None,
        location: str | None = None,
        tz: str | ZoneInfo | None = None,
    ) -> CalendarCreateResult:
        raise NotImplementedError


def _resolve_tz(tz: str | ZoneInfo | None) -> ZoneInfo | None:
    if isinstance(tz, ZoneInfo):
        return tz
    if isinstance(tz, str) and tz.strip():
        try:
            return ZoneInfo(tz.strip())
        except Exception:
            return DEFAULT_TZ
    return None


def _ensure_aware(value: datetime, tz: str | ZoneInfo | None = None) -> datetime:
    if value.tzinfo is not None:
        return value
    tzinfo = _resolve_tz(tz) or DEFAULT_TZ
    return value.replace(tzinfo=tzinfo)


class LocalCalendarBackend:
    def __init__(self, *, chat_id: int, user_id: int, reminders_enabled: bool = False) -> None:
        self._chat_id = chat_id
        self._user_id = user_id
        self._reminders_enabled = reminders_enabled

    async def create_event(
        self,
        title: str,
        start_dt: datetime,
        end_dt: datetime,
        description: str | None = None,
        location: str | None = None,
        tz: str | ZoneInfo | None = None,
    ) -> CalendarCreateResult:
        start_at = _ensure_aware(start_dt, tz)
        _ensure_aware(end_dt, tz)
        created = await calendar_store.add_item(
            dt=start_at,
            title=title,
            chat_id=self._chat_id,
            remind_at=None,
            user_id=self._user_id,
            reminders_enabled=self._reminders_enabled,
        )
        event_payload = created.get("event") if isinstance(created, dict) else None
        event_id = event_payload.get("event_id") if isinstance(event_payload, dict) else None
        if not isinstance(event_id, str) or not event_id:
            raise RuntimeError("local_event_missing_id")
        return CalendarCreateResult(event_id=event_id)


class CalDAVCalendarBackend:
    def __init__(self, config: tools_calendar_caldav.CalDAVConfig, *, chat_id: int, user_id: int) -> None:
        self._config = config
        self._chat_id = chat_id
        self._user_id = user_id

    async def create_event(
        self,
        title: str,
        start_dt: datetime,
        end_dt: datetime,
        description: str | None = None,
        location: str | None = None,
        tz: str | ZoneInfo | None = None,
    ) -> CalendarCreateResult:
        start_at = _ensure_aware(start_dt, tz)
        end_at = _ensure_aware(end_dt, tz)
        created_remote = await tools_calendar_caldav.create_event(
            self._config,
            start_at=start_at,
            end_at=end_at,
            title=title,
            description=description,
            location=location,
            tz=tz,
        )
        event_id = created_remote.uid
        created_local = await calendar_store.add_item(
            dt=start_at,
            title=title,
            chat_id=self._chat_id,
            remind_at=None,
            user_id=self._user_id,
            reminders_enabled=False,
            event_id=event_id,
        )
        event_payload = created_local.get("event") if isinstance(created_local, dict) else None
        stored_id = event_payload.get("event_id") if isinstance(event_payload, dict) else None
        if isinstance(stored_id, str) and stored_id:
            event_id = stored_id
        debug: dict[str, str] = {}
        if created_remote.calendar_name:
            debug["caldav_calendar"] = created_remote.calendar_name
        if created_remote.calendar_url_base:
            debug["caldav_url_base"] = created_remote.calendar_url_base
        if created_remote.uid:
            debug["caldav_uid"] = created_remote.uid
        return CalendarCreateResult(event_id=event_id, debug=debug)
