from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import caldav
import httpx

from app.core import calendar_store
from app.core.calendar_backend import CalendarBackend, CalendarCreateResult

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalDAVBackendConfig:
    url: str
    username: str
    password: str
    calendar_name: str | None


def _sanitize_url_base(url: str) -> str:
    # Remove any userinfo from URL (user:pass@host) to prevent leaks.
    try:
        parts = urlsplit(url)
    except Exception:
        return url
    netloc = parts.netloc
    if "@" in netloc:
        netloc = netloc.split("@", 1)[1]
    sanitized = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return sanitized


def load_caldav_backend_config() -> CalDAVBackendConfig | None:
    url = os.getenv("CALDAV_URL")
    username = os.getenv("CALDAV_USERNAME")
    password = os.getenv("CALDAV_PASSWORD")
    calendar_name = os.getenv("CALDAV_CALENDAR_NAME") or None
    if not url or not username or not password:
        return None
    return CalDAVBackendConfig(
        url=url,
        username=username,
        password=password,
        calendar_name=calendar_name,
    )


def _ensure_aware(value: datetime, *, tz: timezone | None = None) -> datetime:
    if value.tzinfo is not None:
        return value
    # Project default timezone (Europe/Vilnius) is stored in calendar_store.BOT_TZ.
    default_tz = tz or calendar_store.BOT_TZ
    return value.replace(tzinfo=default_tz)


def _format_utc(value: datetime) -> str:
    aware = _ensure_aware(value)
    return aware.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _escape_ical_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _build_ics(
    *,
    uid: str,
    title: str,
    start_dt: datetime,
    end_dt: datetime,
    description: str | None,
    location: str | None,
) -> str:
    dtstamp = datetime.now(timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//laughing-memory//CalDAV//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_format_utc(dtstamp)}",
        f"DTSTART:{_format_utc(start_dt)}",
        f"DTEND:{_format_utc(end_dt)}",
        f"SUMMARY:{_escape_ical_text(title)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_escape_ical_text(description)}")
    if location:
        lines.append(f"LOCATION:{_escape_ical_text(location)}")
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\r\n".join(lines) + "\r\n"


def _calendar_display_name(calendar) -> str | None:
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


def _looks_writable(calendar) -> bool:
    # Best-effort writable check. If property is not available, assume writable.
    try:
        props = calendar.get_properties([caldav.elements.dav.CurrentUserPrivilegeSet()])
        cup = props.get(caldav.elements.dav.CurrentUserPrivilegeSet())
        if cup is None:
            return True
        xml = str(getattr(cup, "xmlelement", cup))
        lowered = xml.lower()
        return "write" in lowered or "write-content" in lowered
    except Exception:
        return True


def _select_calendar(calendars: list, *, preferred_name: str | None) -> tuple[object, str | None]:
    if not calendars:
        raise RuntimeError("no_calendars")

    # Filter writable if possible.
    writable = [cal for cal in calendars if _looks_writable(cal)]
    candidates = writable or calendars

    if preferred_name:
        for cal in candidates:
            name = _calendar_display_name(cal)
            if isinstance(name, str) and name == preferred_name:
                return cal, name

    # Prefer "personal" if present (case-insensitive).
    for cal in candidates:
        name = _calendar_display_name(cal)
        if isinstance(name, str) and name.strip().lower() == "personal":
            return cal, name

    chosen = candidates[0]
    return chosen, _calendar_display_name(chosen)


def _discover_calendar_sync(config: CalDAVBackendConfig) -> tuple[object, str | None]:
    client = caldav.DAVClient(url=config.url, username=config.username, password=config.password)
    principal = client.principal()
    calendars = principal.calendars()
    calendar, display_name = _select_calendar(calendars, preferred_name=config.calendar_name)
    return calendar, display_name


def _safe_error_code(exc: Exception) -> str:
    name = exc.__class__.__name__
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return f"{name}:{status}"
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return f"{name}:{code}"
    return name


class CalDAVCalendarBackend(CalendarBackend):
    name = "caldav"

    def __init__(self, config: CalDAVBackendConfig) -> None:
        self._config = config

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
        if start_dt.tzinfo is None or end_dt.tzinfo is None:
            raise ValueError("naive_datetime")
        if end_dt <= start_dt:
            raise ValueError("invalid_event_range")

        uid = str(uuid.uuid4())
        ics = _build_ics(
            uid=uid,
            title=title,
            start_dt=start_dt,
            end_dt=end_dt,
            description=description,
            location=location,
        )
        calendar, calendar_name = await asyncio.to_thread(_discover_calendar_sync, self._config)

        cal_url = str(getattr(calendar, "url", "") or "")
        if not cal_url:
            raise RuntimeError("missing_calendar_url")
        if not cal_url.endswith("/"):
            cal_url = cal_url + "/"
        event_url = f"{cal_url}{uid}.ics"

        url_base = _sanitize_url_base(self._config.url)
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                auth=(self._config.username, self._config.password),
                follow_redirects=True,
                headers={"Content-Type": "text/calendar; charset=utf-8"},
            ) as client:
                resp = await client.put(event_url, content=ics.encode("utf-8"))
        except Exception as exc:
            # Never log secrets; surface only safe error code via exception.
            raise RuntimeError(f"caldav_put_failed:{_safe_error_code(exc)}") from exc

        if resp.status_code not in {200, 201, 204}:
            # Avoid including response text (could include server diagnostics).
            raise RuntimeError(f"caldav_put_status:{resp.status_code}")

        return CalendarCreateResult(
            uid=uid,
            debug={
                "calendar_backend": "caldav",
                "caldav_calendar": calendar_name,
                "caldav_uid": uid,
                "caldav_url_base": url_base,
                "tz": tz or str(calendar_store.BOT_TZ),
            },
        )

