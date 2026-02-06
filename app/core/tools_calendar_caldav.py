from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import caldav
import httpx
from icalendar import Calendar

from app.core import calendar_store

LOGGER = logging.getLogger(__name__)

DEFAULT_TZ = calendar_store.BOT_TZ


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
    calendar_name: str | None = None
    calendar_url_base: str | None = None


class CalDAVRequestError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"caldav_request_failed:{status_code}")
        self.status_code = status_code


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
    end_at: datetime | None = None,
    title: str,
    description: str | None = None,
    location: str | None = None,
    tz: str | None = None,
) -> CreatedEvent:
    return await asyncio.to_thread(_create_event_sync, config, start_at, end_at, title, description, location, tz)


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
        calendar, name = _resolve_calendar(config)
        return True, name
    except Exception as exc:
        LOGGER.warning("CalDAV connection failed: %s", exc.__class__.__name__)
        return False, None


def _create_event_sync(
    config: CalDAVConfig,
    start_at: datetime,
    end_at: datetime | None,
    title: str,
    description: str | None,
    location: str | None,
    tz: str | None,
) -> CreatedEvent:
    calendar, calendar_name = _resolve_calendar(config)
    start_utc = _to_utc(_ensure_aware(start_at, tz))
    end_value = end_at if isinstance(end_at, datetime) else start_at + timedelta(hours=1)
    end_utc = _to_utc(_ensure_aware(end_value, tz))
    uid = str(uuid.uuid4())
    ical = _build_ical_event(
        uid=uid,
        start_at=start_utc,
        end_at=end_utc,
        title=title,
        description=description,
        location=location,
    )
    calendar_url = _calendar_url(calendar)
    href = _build_event_url(calendar_url, uid)
    _put_event(href, ical, config)
    return CreatedEvent(
        uid=uid,
        href=href,
        calendar_name=calendar_name,
        calendar_url_base=_safe_url_base(calendar_url or config.url),
    )


def _list_events_sync(
    config: CalDAVConfig,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[CalDAVEvent]:
    calendar, _ = _resolve_calendar(config)
    start_utc = _to_utc(start)
    end_utc = _to_utc(end)
    events = calendar.date_search(start_utc, end_utc)
    parsed = [_parse_event(event) for event in events]
    items = [item for item in parsed if item is not None]
    items.sort(key=lambda item: item.start_at)
    return items[:limit]


def _delete_event_sync(config: CalDAVConfig, event_id: str) -> bool:
    calendar, _ = _resolve_calendar(config)
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


def _resolve_calendar(config: CalDAVConfig) -> tuple[object, str | None]:
    client = caldav.DAVClient(url=config.url, username=config.username, password=config.password)
    principal = client.principal()
    calendars = principal.calendars()
    if not calendars:
        raise RuntimeError("no_calendars")
    writable = [calendar for calendar in calendars if _calendar_is_writable(calendar)]
    selected = _select_calendar(writable or calendars, config.calendar_name)
    if selected is None and writable:
        selected = writable[0]
    if selected is None:
        raise RuntimeError("no_writable_calendars")
    name = _calendar_name(selected)
    if not name and config.calendar_name:
        name = config.calendar_name
    return selected, name


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


def _calendar_is_writable(calendar) -> bool:
    for attr in ("writeable", "writable", "read_only", "readonly", "is_readonly"):
        value = getattr(calendar, attr, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if isinstance(value, bool):
            return value if attr in {"writeable", "writable"} else not value
    try:
        props = calendar.get_properties([caldav.elements.dav.CurrentUserPrivilegeSet()])
    except Exception:
        return True
    privileges = props.get(caldav.elements.dav.CurrentUserPrivilegeSet())
    if privileges is None:
        return True
    text = str(privileges)
    return "write" in text.lower()


def _select_calendar(calendars: list[object], preferred_name: str | None) -> object | None:
    if not calendars:
        return None
    if preferred_name:
        target = preferred_name.strip().lower()
        for calendar in calendars:
            name = _calendar_name(calendar)
            if isinstance(name, str) and name.strip().lower() == target:
                return calendar
    for calendar in calendars:
        name = _calendar_name(calendar)
        if isinstance(name, str) and name.strip().lower() == "personal":
            return calendar
    return calendars[0]


def _ensure_aware(value: datetime, tz: str | None) -> datetime:
    if value.tzinfo is not None:
        return value
    tzinfo = DEFAULT_TZ
    if tz:
        try:
            tzinfo = ZoneInfo(tz)
        except Exception:
            tzinfo = DEFAULT_TZ
    return value.replace(tzinfo=tzinfo)


def _calendar_url(calendar) -> str | None:
    url_attr = getattr(calendar, "url", None)
    if url_attr:
        return str(url_attr)
    href_attr = getattr(calendar, "href", None)
    if href_attr:
        return str(href_attr)
    return None


def _safe_url_base(url: str) -> str:
    parts = urlsplit(url)
    hostname = parts.hostname or ""
    netloc = hostname
    if parts.port:
        netloc = f"{hostname}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _build_event_url(calendar_url: str | None, uid: str) -> str:
    if not calendar_url:
        raise RuntimeError("calendar_url_missing")
    safe_base = _safe_url_base(calendar_url)
    base = safe_base if safe_base.endswith("/") else f"{safe_base}/"
    return urljoin(base, f"{uid}.ics")


def _put_event(url: str, ical: str, config: CalDAVConfig) -> None:
    headers = {"Content-Type": "text/calendar; charset=utf-8"}
    with httpx.Client(auth=(config.username, config.password), timeout=10.0) as client:
        response = client.put(url, content=ical.encode("utf-8"), headers=headers)
    if response.status_code not in {200, 201, 204}:
        raise CalDAVRequestError(response.status_code)


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
        return _decode_ical_value(value)
    return _decode_ical_value(decoded)


def _decode_ical_value(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def _normalize_datetime(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        return _to_utc(value)
    return _to_utc(datetime.combine(value, datetime.min.time()))


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=DEFAULT_TZ)
    return value.astimezone(timezone.utc)


def _build_ical_event(
    *,
    uid: str,
    start_at: datetime,
    end_at: datetime,
    title: str,
    description: str | None,
    location: str | None,
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
    if location:
        lines.append(f"LOCATION:{_escape_ical_text(location)}")
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\r\n".join(lines) + "\r\n"


def _format_utc(value: datetime) -> str:
    return _to_utc(value).strftime("%Y%m%dT%H%M%SZ")


def _escape_ical_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
