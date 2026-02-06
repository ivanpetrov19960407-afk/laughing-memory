"""Tests for CalendarBackend abstraction (Stage 6 — CalDAV integration).

Covers:
- Successful create_event via CalDAV mock
- CalDAV error → fallback to LocalCalendarBackend
- Secret/password leak prevention in text/debug/logs
- OrchestratorResult contract fields
- Backend factory (get_backend)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.core.calendar_backend import (
    CalDAVCalendarBackend,
    CalendarBackend,
    CalendarCreateResult,
    LocalCalendarBackend,
    _strip_password_from_url,
    get_backend,
)
from app.core import calendar_store, tools_calendar_caldav
from app.core.tools_calendar import create_event as tool_create_event
from app.core.result import OrchestratorResult

BOT_TZ = ZoneInfo("Europe/Vilnius")

# ---------------------------------------------------------------------------
# Fakes for CalDAV layer
# ---------------------------------------------------------------------------

class FakeCreatedEvent:
    def __init__(self, uid: str, href: str | None = None):
        self.uid = uid
        self.href = href


class FakeCalendar:
    def __init__(self, name: str) -> None:
        self.name = name
        self.added: str | None = None

    def add_event(self, ical: str):
        self.added = ical
        return MagicMock(url="https://caldav.example.com/event/1.ics")


class FakePrincipal:
    def __init__(self, calendars):
        self._calendars = calendars

    def calendars(self):
        return list(self._calendars)


class FakeDAVClient:
    def __init__(self, calendars):
        self._principal = FakePrincipal(calendars)

    def principal(self):
        return self._principal


# ---------------------------------------------------------------------------
# Backend factory tests
# ---------------------------------------------------------------------------

class TestGetBackend:
    def test_default_is_local(self, monkeypatch) -> None:
        monkeypatch.delenv("CALENDAR_BACKEND", raising=False)
        monkeypatch.delenv("CALDAV_URL", raising=False)
        backend = get_backend()
        assert isinstance(backend, LocalCalendarBackend)

    def test_explicit_local(self, monkeypatch) -> None:
        monkeypatch.setenv("CALENDAR_BACKEND", "local")
        backend = get_backend()
        assert isinstance(backend, LocalCalendarBackend)

    def test_caldav_with_full_config(self, monkeypatch) -> None:
        monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
        monkeypatch.setenv("CALDAV_URL", "https://nc.example.com/remote.php/dav")
        monkeypatch.setenv("CALDAV_USERNAME", "alice")
        monkeypatch.setenv("CALDAV_PASSWORD", "secret-app-pass")
        backend = get_backend()
        assert isinstance(backend, CalDAVCalendarBackend)

    def test_caldav_without_password_falls_back(self, monkeypatch) -> None:
        monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
        monkeypatch.setenv("CALDAV_URL", "https://nc.example.com/remote.php/dav")
        monkeypatch.setenv("CALDAV_USERNAME", "alice")
        monkeypatch.delenv("CALDAV_PASSWORD", raising=False)
        backend = get_backend()
        assert isinstance(backend, LocalCalendarBackend)

    def test_invalid_backend_value_falls_back(self, monkeypatch) -> None:
        monkeypatch.setenv("CALENDAR_BACKEND", "google")
        backend = get_backend()
        assert isinstance(backend, LocalCalendarBackend)


# ---------------------------------------------------------------------------
# LocalCalendarBackend tests
# ---------------------------------------------------------------------------

class TestLocalCalendarBackend:
    def test_create_event_returns_success(self) -> None:
        backend = LocalCalendarBackend()
        result = asyncio.run(
            backend.create_event(
                "Team standup",
                datetime(2026, 3, 1, 10, 0, tzinfo=BOT_TZ),
                datetime(2026, 3, 1, 11, 0, tzinfo=BOT_TZ),
            )
        )
        assert result.success is True
        assert result.backend == "local"
        assert isinstance(result.event_id, str)
        assert len(result.event_id) > 0

    def test_create_event_generates_unique_ids(self) -> None:
        backend = LocalCalendarBackend()
        ids = set()
        for _ in range(50):
            result = asyncio.run(
                backend.create_event(
                    "Test",
                    datetime(2026, 3, 1, 10, 0, tzinfo=BOT_TZ),
                    datetime(2026, 3, 1, 11, 0, tzinfo=BOT_TZ),
                )
            )
            ids.add(result.event_id)
        assert len(ids) == 50


# ---------------------------------------------------------------------------
# CalDAVCalendarBackend tests
# ---------------------------------------------------------------------------

class TestCalDAVCalendarBackend:
    def test_create_event_success(self, monkeypatch) -> None:
        calendar = FakeCalendar("personal")
        fake_client = FakeDAVClient([calendar])
        monkeypatch.setattr(
            "app.core.tools_calendar_caldav.caldav.DAVClient",
            lambda *args, **kwargs: fake_client,
        )

        backend = CalDAVCalendarBackend(
            url="https://nc.example.com/remote.php/dav",
            username="alice",
            password="secret-app-pass",
            calendar_name=None,
        )
        result = asyncio.run(
            backend.create_event(
                "Doctor appointment",
                datetime(2026, 3, 1, 14, 0, tzinfo=BOT_TZ),
                datetime(2026, 3, 1, 15, 0, tzinfo=BOT_TZ),
            )
        )

        assert result.success is True
        assert result.backend == "caldav"
        assert isinstance(result.uid, str) and len(result.uid) > 0
        assert result.debug.get("calendar_backend") == "caldav"
        assert isinstance(result.debug.get("caldav_uid"), str)
        assert "secret" not in str(result.debug)
        assert "app-pass" not in str(result.debug)
        assert calendar.added is not None

    def test_create_event_with_named_calendar(self, monkeypatch) -> None:
        personal = FakeCalendar("personal")
        work = FakeCalendar("work")
        fake_client = FakeDAVClient([personal, work])
        monkeypatch.setattr(
            "app.core.tools_calendar_caldav.caldav.DAVClient",
            lambda *args, **kwargs: fake_client,
        )

        backend = CalDAVCalendarBackend(
            url="https://nc.example.com/remote.php/dav",
            username="alice",
            password="secret",
            calendar_name="work",
        )
        result = asyncio.run(
            backend.create_event(
                "Meeting",
                datetime(2026, 3, 1, 9, 0, tzinfo=BOT_TZ),
                datetime(2026, 3, 1, 10, 0, tzinfo=BOT_TZ),
            )
        )

        assert result.success is True
        assert work.added is not None
        assert personal.added is None

    def test_safe_url_strips_password(self) -> None:
        backend = CalDAVCalendarBackend(
            url="https://alice:supersecret@nc.example.com/dav",
            username="alice",
            password="supersecret",
        )
        assert "supersecret" not in backend.safe_url
        assert "alice" in backend.safe_url

    def test_create_event_propagates_exception(self, monkeypatch) -> None:
        """CalDAV connection error should propagate (caller handles fallback)."""
        def raise_error(*args, **kwargs):
            raise ConnectionError("network failure")

        monkeypatch.setattr(
            "app.core.tools_calendar_caldav.caldav.DAVClient",
            raise_error,
        )

        backend = CalDAVCalendarBackend(
            url="https://nc.example.com/remote.php/dav",
            username="alice",
            password="secret",
        )
        with pytest.raises(ConnectionError):
            asyncio.run(
                backend.create_event(
                    "Test",
                    datetime(2026, 3, 1, 10, 0, tzinfo=BOT_TZ),
                    datetime(2026, 3, 1, 11, 0, tzinfo=BOT_TZ),
                )
            )


# ---------------------------------------------------------------------------
# tools_calendar.create_event integration tests
# ---------------------------------------------------------------------------

class TestToolCreateEventWithBackend:
    """Test the full flow through tools_calendar.create_event with backends."""

    def test_caldav_success_returns_ok_with_debug(self, tmp_path, monkeypatch) -> None:
        calendar_path = tmp_path / "calendar.json"
        monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
        monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
        monkeypatch.setenv("CALDAV_URL", "https://nc.example.com/remote.php/dav")
        monkeypatch.setenv("CALDAV_USERNAME", "alice")
        monkeypatch.setenv("CALDAV_PASSWORD", "secret-app-pass")

        calendar = FakeCalendar("personal")
        fake_client = FakeDAVClient([calendar])
        monkeypatch.setattr(
            "app.core.tools_calendar_caldav.caldav.DAVClient",
            lambda *args, **kwargs: fake_client,
        )

        result = asyncio.run(
            tool_create_event(
                start_at=datetime(2026, 3, 1, 14, 0, tzinfo=BOT_TZ),
                title="Dentist",
                chat_id=100,
                user_id=42,
                request_id="req-caldav-1",
                intent="calendar.add",
            )
        )

        assert isinstance(result, OrchestratorResult)
        assert result.status == "ok"
        assert result.intent == "calendar.add"
        assert result.mode == "tool"
        assert "Событие создано" in result.text
        assert "Dentist" in result.text
        assert result.debug.get("calendar_backend") == "caldav"
        assert isinstance(result.debug.get("caldav_uid"), str)
        assert isinstance(result.debug.get("caldav_calendar"), str)
        # Verify password not leaked
        debug_str = str(result.debug)
        assert "secret-app-pass" not in debug_str
        result.validate()

    def test_caldav_failure_falls_back_to_local(self, tmp_path, monkeypatch) -> None:
        calendar_path = tmp_path / "calendar.json"
        monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
        monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
        monkeypatch.setenv("CALDAV_URL", "https://nc.example.com/remote.php/dav")
        monkeypatch.setenv("CALDAV_USERNAME", "alice")
        monkeypatch.setenv("CALDAV_PASSWORD", "secret-app-pass")

        def raise_error(*args, **kwargs):
            raise ConnectionError("network down")

        monkeypatch.setattr(
            "app.core.tools_calendar_caldav.caldav.DAVClient",
            raise_error,
        )

        result = asyncio.run(
            tool_create_event(
                start_at=datetime(2026, 3, 1, 14, 0, tzinfo=BOT_TZ),
                title="Dentist",
                chat_id=100,
                user_id=42,
                request_id="req-fallback-1",
                intent="calendar.add",
            )
        )

        assert isinstance(result, OrchestratorResult)
        assert result.status == "ok"
        assert result.mode == "tool"
        assert "Событие создано" in result.text
        assert result.debug.get("calendar_backend") == "local_fallback"
        assert isinstance(result.debug.get("caldav_error"), str)
        # No password in error
        assert "secret-app-pass" not in str(result.debug.get("caldav_error", ""))
        result.validate()

    def test_local_backend_creates_event_ok(self, tmp_path, monkeypatch) -> None:
        calendar_path = tmp_path / "calendar.json"
        monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
        monkeypatch.setenv("CALENDAR_BACKEND", "local")

        result = asyncio.run(
            tool_create_event(
                start_at=datetime(2026, 3, 1, 14, 0, tzinfo=BOT_TZ),
                title="Local event",
                chat_id=100,
                user_id=42,
                request_id="req-local-1",
                intent="calendar.add",
            )
        )

        assert isinstance(result, OrchestratorResult)
        assert result.status == "ok"
        assert "Событие создано" in result.text
        assert result.debug.get("calendar_backend") == "local"
        result.validate()

        # Verify event is in the store
        store = calendar_store.load_store()
        events = store.get("events") or []
        assert len(events) >= 1

    def test_default_backend_is_local(self, tmp_path, monkeypatch) -> None:
        """When CALENDAR_BACKEND is not set, use local."""
        calendar_path = tmp_path / "calendar.json"
        monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
        monkeypatch.delenv("CALENDAR_BACKEND", raising=False)
        monkeypatch.delenv("CALDAV_URL", raising=False)

        result = asyncio.run(
            tool_create_event(
                start_at=datetime(2026, 3, 1, 14, 0, tzinfo=BOT_TZ),
                title="Default backend test",
                chat_id=100,
                user_id=42,
                intent="calendar.add",
            )
        )
        assert result.status == "ok"
        result.validate()


# ---------------------------------------------------------------------------
# Secret leak prevention tests
# ---------------------------------------------------------------------------

class TestNoSecretLeaks:
    """Ensure passwords never appear in text, debug, or log output."""

    SECRET_PASSWORD = "my-super-secret-app-password-123"

    def test_password_not_in_debug_on_success(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("CALENDAR_PATH", str(tmp_path / "cal.json"))
        monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
        monkeypatch.setenv("CALDAV_URL", "https://nc.example.com/remote.php/dav")
        monkeypatch.setenv("CALDAV_USERNAME", "user")
        monkeypatch.setenv("CALDAV_PASSWORD", self.SECRET_PASSWORD)

        calendar = FakeCalendar("personal")
        fake_client = FakeDAVClient([calendar])
        monkeypatch.setattr(
            "app.core.tools_calendar_caldav.caldav.DAVClient",
            lambda *args, **kwargs: fake_client,
        )

        result = asyncio.run(
            tool_create_event(
                start_at=datetime(2026, 4, 1, 10, 0, tzinfo=BOT_TZ),
                title="Secret test",
                chat_id=1,
                user_id=1,
            )
        )

        # Check text and debug
        assert self.SECRET_PASSWORD not in result.text
        assert self.SECRET_PASSWORD not in str(result.debug)
        # Check full serialization
        assert self.SECRET_PASSWORD not in result.to_public_json()
        assert self.SECRET_PASSWORD not in result.to_log_json()

    def test_password_not_in_debug_on_caldav_error(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("CALENDAR_PATH", str(tmp_path / "cal.json"))
        monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
        monkeypatch.setenv("CALDAV_URL", "https://nc.example.com/remote.php/dav")
        monkeypatch.setenv("CALDAV_USERNAME", "user")
        monkeypatch.setenv("CALDAV_PASSWORD", self.SECRET_PASSWORD)

        def raise_error(*args, **kwargs):
            raise RuntimeError(f"auth failed for {self.SECRET_PASSWORD}")

        monkeypatch.setattr(
            "app.core.tools_calendar_caldav.caldav.DAVClient",
            raise_error,
        )

        result = asyncio.run(
            tool_create_event(
                start_at=datetime(2026, 4, 1, 10, 0, tzinfo=BOT_TZ),
                title="Secret test error",
                chat_id=1,
                user_id=1,
            )
        )

        assert self.SECRET_PASSWORD not in result.text
        assert self.SECRET_PASSWORD not in str(result.debug)
        assert self.SECRET_PASSWORD not in result.to_public_json()
        assert self.SECRET_PASSWORD not in result.to_log_json()

    def test_password_not_in_logs(self, tmp_path, monkeypatch, caplog) -> None:
        monkeypatch.setenv("CALENDAR_PATH", str(tmp_path / "cal.json"))
        monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
        monkeypatch.setenv("CALDAV_URL", "https://nc.example.com/remote.php/dav")
        monkeypatch.setenv("CALDAV_USERNAME", "user")
        monkeypatch.setenv("CALDAV_PASSWORD", self.SECRET_PASSWORD)

        def raise_error(*args, **kwargs):
            raise ConnectionError("connection refused")

        monkeypatch.setattr(
            "app.core.tools_calendar_caldav.caldav.DAVClient",
            raise_error,
        )

        with caplog.at_level(logging.DEBUG):
            asyncio.run(
                tool_create_event(
                    start_at=datetime(2026, 4, 1, 10, 0, tzinfo=BOT_TZ),
                    title="Log test",
                    chat_id=1,
                    user_id=1,
                )
            )

        full_log = caplog.text
        assert self.SECRET_PASSWORD not in full_log

    def test_caldav_backend_safe_url(self) -> None:
        backend = CalDAVCalendarBackend(
            url="https://user:my-secret-pass@nc.example.com/dav",
            username="user",
            password="my-secret-pass",
        )
        assert "my-secret-pass" not in backend.safe_url
        assert "***" in backend.safe_url

    def test_strip_password_from_url_util(self) -> None:
        assert "pass123" not in _strip_password_from_url("https://user:pass123@host.com/dav")
        # No credentials — unchanged
        plain = "https://host.com/remote.php/dav"
        assert _strip_password_from_url(plain) == plain


# ---------------------------------------------------------------------------
# OrchestratorResult contract tests
# ---------------------------------------------------------------------------

class TestOrchestratorResultContract:
    """Verify the result matches the specified contract."""

    def test_success_caldav_contract(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("CALENDAR_PATH", str(tmp_path / "cal.json"))
        monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
        monkeypatch.setenv("CALDAV_URL", "https://nc.example.com/remote.php/dav")
        monkeypatch.setenv("CALDAV_USERNAME", "user")
        monkeypatch.setenv("CALDAV_PASSWORD", "pass")

        calendar = FakeCalendar("personal")
        fake_client = FakeDAVClient([calendar])
        monkeypatch.setattr(
            "app.core.tools_calendar_caldav.caldav.DAVClient",
            lambda *args, **kwargs: fake_client,
        )

        result = asyncio.run(
            tool_create_event(
                start_at=datetime(2026, 5, 1, 10, 0, tzinfo=BOT_TZ),
                title="Contract test",
                chat_id=100,
                user_id=42,
                intent="calendar.add",
            )
        )

        # Contract fields
        assert result.status == "ok"
        assert result.intent == "calendar.add"
        assert result.mode == "tool"
        assert isinstance(result.text, str) and len(result.text) > 0
        assert result.debug["calendar_backend"] == "caldav"
        assert isinstance(result.debug["caldav_calendar"], str)
        assert isinstance(result.debug["caldav_uid"], str)
        assert isinstance(result.debug["caldav_url_base"], str)
        # Validate passes
        result.validate()

    def test_fallback_contract(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("CALENDAR_PATH", str(tmp_path / "cal.json"))
        monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
        monkeypatch.setenv("CALDAV_URL", "https://nc.example.com/remote.php/dav")
        monkeypatch.setenv("CALDAV_USERNAME", "user")
        monkeypatch.setenv("CALDAV_PASSWORD", "pass")

        monkeypatch.setattr(
            "app.core.tools_calendar_caldav.caldav.DAVClient",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")),
        )

        result = asyncio.run(
            tool_create_event(
                start_at=datetime(2026, 5, 1, 10, 0, tzinfo=BOT_TZ),
                title="Fallback contract",
                chat_id=100,
                user_id=42,
                intent="calendar.add",
            )
        )

        assert result.status == "ok"
        assert result.debug["calendar_backend"] == "local_fallback"
        assert isinstance(result.debug.get("caldav_error"), str)
        result.validate()
