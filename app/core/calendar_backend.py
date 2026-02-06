"""Calendar backend abstraction.

Provides CalendarBackend interface with two implementations:
- LocalCalendarBackend  — stores events in local JSON file (calendar_store)
- CalDAVCalendarBackend — creates events via CalDAV (Nextcloud-compatible)

Factory ``get_backend()`` selects implementation based on CALENDAR_BACKEND env.
"""

from __future__ import annotations

import abc
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger(__name__)

# Project-wide default timezone (Europe/Vilnius).
DEFAULT_TZ = ZoneInfo(os.getenv("BOT_TIMEZONE", "Europe/Vilnius"))


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CalendarCreateResult:
    """Unified result of ``create_event``."""
    success: bool
    event_id: str | None = None
    uid: str | None = None
    calendar_name: str | None = None
    backend: str = "local"  # "local" | "caldav" | "local_fallback"
    error: str | None = None
    debug: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class CalendarBackend(abc.ABC):
    """Abstract calendar backend."""

    @abc.abstractmethod
    async def create_event(
        self,
        title: str,
        start_dt: datetime,
        end_dt: datetime,
        *,
        description: str | None = None,
        location: str | None = None,
        tz: ZoneInfo | None = None,
    ) -> CalendarCreateResult:
        """Create a calendar event and return the result."""
        ...


# ---------------------------------------------------------------------------
# Local (JSON file) backend
# ---------------------------------------------------------------------------

class LocalCalendarBackend(CalendarBackend):
    """Local-only backend — generates a UID.

    Actual persistence into the JSON store is done by the caller
    (``tools_calendar.create_event``) via ``calendar_store.add_item``.
    """

    async def create_event(
        self,
        title: str,
        start_dt: datetime,
        end_dt: datetime,
        *,
        description: str | None = None,
        location: str | None = None,
        tz: ZoneInfo | None = None,
    ) -> CalendarCreateResult:
        event_id = uuid.uuid4().hex[:8]
        return CalendarCreateResult(
            success=True,
            event_id=event_id,
            uid=event_id,
            backend="local",
            debug={"calendar_backend": "local"},
        )


# ---------------------------------------------------------------------------
# CalDAV backend
# ---------------------------------------------------------------------------

class CalDAVCalendarBackend(CalendarBackend):
    """Creates events via CalDAV (Nextcloud-compatible).

    Uses :mod:`app.core.tools_calendar_caldav` under the hood.
    Passwords / secrets are **never** placed into logs or debug output.
    """

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        calendar_name: str | None = None,
    ) -> None:
        self._url = url
        self._username = username
        self._password = password
        self._calendar_name = calendar_name

    # -- public helpers (safe for debug) --
    @property
    def safe_url(self) -> str:
        """URL with password stripped (safe for logs/debug)."""
        return _strip_password_from_url(self._url)

    async def create_event(
        self,
        title: str,
        start_dt: datetime,
        end_dt: datetime,
        *,
        description: str | None = None,
        location: str | None = None,
        tz: ZoneInfo | None = None,
    ) -> CalendarCreateResult:
        from app.core import tools_calendar_caldav

        start_aware = _ensure_aware(start_dt, tz)
        end_aware = _ensure_aware(end_dt, tz)

        config = tools_calendar_caldav.CalDAVConfig(
            url=self._url,
            username=self._username,
            password=self._password,
            calendar_name=self._calendar_name,
        )
        created = await tools_calendar_caldav.create_event(
            config,
            start_at=start_aware,
            title=title,
            description=description,
        )
        # Resolve calendar name for debug
        cal_name = self._calendar_name or "(default)"
        try:
            ok_flag, resolved_name = await tools_calendar_caldav.check_connection(config)
            if ok_flag and resolved_name:
                cal_name = resolved_name
        except Exception:
            pass

        return CalendarCreateResult(
            success=True,
            event_id=created.uid,
            uid=created.uid,
            calendar_name=cal_name,
            backend="caldav",
            debug={
                "calendar_backend": "caldav",
                "caldav_calendar": cal_name,
                "caldav_uid": created.uid,
                "caldav_url_base": self.safe_url,
            },
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_backend() -> CalendarBackend:
    """Return the configured CalendarBackend based on env vars.

    * ``CALENDAR_BACKEND=caldav`` and valid CALDAV_URL/USERNAME/PASSWORD → CalDAVCalendarBackend
    * otherwise → LocalCalendarBackend
    """
    backend_type = os.getenv("CALENDAR_BACKEND", "local").strip().lower()
    if backend_type == "caldav":
        url = os.getenv("CALDAV_URL", "").strip()
        username = os.getenv("CALDAV_USERNAME", "").strip()
        password = os.getenv("CALDAV_PASSWORD", "").strip()
        if url and username and password:
            cal_name = os.getenv("CALDAV_CALENDAR_NAME") or None
            LOGGER.info(
                "Calendar backend: caldav url_base=%s calendar=%s",
                _strip_password_from_url(url),
                cal_name or "(auto)",
            )
            return CalDAVCalendarBackend(
                url=url,
                username=username,
                password=password,
                calendar_name=cal_name,
            )
        LOGGER.warning(
            "CALENDAR_BACKEND=caldav but CALDAV_URL/USERNAME/PASSWORD incomplete; falling back to local"
        )
    return LocalCalendarBackend()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_aware(dt: datetime, tz: ZoneInfo | None = None) -> datetime:
    """Ensure *dt* is timezone-aware; apply *tz* or DEFAULT_TZ if naive."""
    if dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=tz or DEFAULT_TZ)


def _strip_password_from_url(url: str) -> str:
    """Remove password from URL string (e.g. ``http://user:pass@host``)."""
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", url)
