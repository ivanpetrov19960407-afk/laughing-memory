from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import caldav
from icalendar import Calendar

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalDAVConfig:
    url: str
    username: str
    password: str
    calendar_name: str | None = None


@dataclass(frozen=True)
class CalDAVEvent:
    uid: str
    summary: str
    start_at: datetime


@dataclass(frozen=True)
class CreatedEvent:
    uid: str
    href: str | None


def load_caldav_config() -> CalDAVConfig | None:
    url = os.getenv("CALDAV_URL")
    username = os.getenv("CALDAV_USERNAME")
    password = os.getenv("CALDAV_PASSWORD")
    calendar_name = os.getenv("CALDAV_CALENDAR_NAME") or None
    if not url or not username or not password:
        return None
    return CalDAVConfig(
        url=url,
        username=username,
        password=password,
        calendar_name=calendar_name,
    )


async def check_connection(config: CalDAVConfig) -> tuple[bool, str | None]:
    return await asyncio.to_thread(_check_connection_sync, config)


async def create_event(
    config: CalDAVConfig,
    *,
    start_at: datetime,
    title: str,
    description: str | None = None,
) -> CreatedEvent:
    return await asyncio.to_thread(_create_event_sync, config, start_at, title, description)


async def list_events(
    config: CalDAVConfig,
    *,
    start: datetime,
    end: datetime,
    limit: int = 20,
) -> list[CalDAVEvent]:
    return await asyncio.to_thread(_list_events_sync, config, start, end, limit)


async def delete_event(config: CalDAVConfig, *, event_id: str) -> bool:
    return await asyncio.to_thread(_delete_event_sync, config, event_id)


def _check_connection_sync(config: CalDAVConfig) -> tuple[bool, str | None]:
    try:
        calendar = _resolve_calendar(config)
        name = _calendar_name(calendar)
        return True, name
    except Exception as exc:
        LOGGER.warning("CalDAV connection failed: %s", exc.__class__.__name__)
        return False, None


def _create_event_sync(
    config: CalDAVConfig,
    start_at: datetime,
    title: str,
    description: str | None,
) -> CreatedEvent:
    calendar = _resolve_calendar(config)
    start_utc = _to_utc(start_at)
    end_utc = start_utc + timedelta(hours=1)
    uid = str(uuid.uuid4())
    ical = _build_ical_event(uid=uid, start_at=start_utc, end_at=end_utc, title=title, description=description)
    created = calendar.add_event(ical)
    href = _event_href(created)
    return CreatedEvent(uid=uid, href=href)


def _list_events_sync(
    config: CalDAVConfig,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[CalDAVEvent]:
    calendar = _resolve_calendar(config)
    start_utc = _to_utc(start)
    end_utc = _to_utc(end)
    events = calendar.date_search(start_utc, end_utc)
    parsed = [_parse_event(event) for event in events]
    items = [item for item in parsed if item is not None]
    items.sort(key=lambda item: item.start_at)
    return items[:limit]


def _delete_event_sync(config: CalDAVConfig, event_id: str) -> bool:
    calendar = _resolve_calendar(config)
    by_uid = getattr(calendar, "event_by_uid", None)
    if callable(by_uid):
        try:
            event = by_uid(event_id)
        except Exception:
            event = None
        if event is not None:
            event.delete()
            return True
    by_url = getattr(calendar, "event_by_url", None)
    if callable(by_url):
        try:
            event = by_url(event_id)
        except Exception:
            event = None
        if event is not None:
            event.delete()
            return True
    try:
        events: Iterable = calendar.events()
    except Exception:
        events = []
    for event in events:
        item = _parse_event(event)
        if item is None:
            continue
        if item.uid == event_id:
            event.delete()
            return True
    return False


def _resolve_calendar(config: CalDAVConfig):
    client = caldav.DAVClient(url=config.url, username=config.username, password=config.password)
    principal = client.principal()
    calendars = principal.calendars()
    if not calendars:
        raise RuntimeError("no_calendars")
    if config.calendar_name:
        for calendar in calendars:
            name = _calendar_name(calendar)
            if name == config.calendar_name:
                return calendar
    return calendars[0]


def _calendar_name(calendar) -> str | None:
    name_attr = getattr(calendar, "name", None)
    name = name_attr() if callable(name_attr) else name_attr
    if isinstance(name, str) and name:
        return name
    try:
        props = calendar.get_properties([caldav.elements.dav.DisplayName()])
    except Exception:
        return None
    display = props.get(caldav.elements.dav.DisplayName())
    if display is None:
        return None
    value = display.value if hasattr(display, "value") else display
    return str(value) if value else None


def _event_href(event) -> str | None:
    if event is None:
        return None
    for attr in ("url", "href"):
        value = getattr(event, attr, None)
        if value:
            return str(value)
    return None


def _parse_event(event) -> CalDAVEvent | None:
    data = getattr(event, "data", None)
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="ignore")
    if not isinstance(data, str) or not data.strip():
        return None
    calendar = Calendar.from_ical(data)
    vevent = None
    for component in calendar.walk():
        if component.name == "VEVENT":
            vevent = component
            break
    if vevent is None:
        return None
    uid = _safe_ical_value(vevent, "uid")
    summary = _safe_ical_value(vevent, "summary") or "(без названия)"
    start_raw = vevent.decoded("dtstart", None)
    if start_raw is None:
        return None
    start_at = _normalize_datetime(start_raw)
    if not uid:
        uid = str(uuid.uuid4())
    return CalDAVEvent(uid=uid, summary=summary, start_at=start_at)


def _safe_ical_value(vevent, name: str) -> str | None:
    value = vevent.get(name)
    if value is None:
        return None
    decoded = vevent.decoded(name, None)
    if decoded is None:
        return str(value)
    return str(decoded)


def _normalize_datetime(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        return _to_utc(value)
    return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _build_ical_event(
    *,
    uid: str,
    start_at: datetime,
    end_at: datetime,
    title: str,
    description: str | None,
) -> str:
    dtstamp = datetime.now(timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//laughing-memory//CalDAV//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_format_utc(dtstamp)}",
        f"DTSTART:{_format_utc(start_at)}",
        f"DTEND:{_format_utc(end_at)}",
        f"SUMMARY:{_escape_ical_text(title)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_escape_ical_text(description)}")
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\r\n".join(lines) + "\r\n"


def _format_utc(value: datetime) -> str:
    return _to_utc(value).strftime("%Y%m%dT%H%M%SZ")


def _escape_ical_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
