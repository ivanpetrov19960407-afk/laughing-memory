from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.core import calendar_backend, calendar_store
from app.core.tools_calendar import create_event, delete_event, list_calendar_items


def test_calendar_tool_creates_event_with_local_backend(tmp_path, monkeypatch) -> None:
    """Test that local backend works when CALENDAR_BACKEND=local"""
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "local")

    result = asyncio.run(
        create_event(
            start_at=datetime(2026, 2, 5, 18, 30, tzinfo=calendar_store.BOT_TZ),
            title="Врач",
            chat_id=10,
            user_id=1,
            request_id="req-1",
            intent="utility_calendar.add",
        )
    )

    assert result.status == "ok"
    assert "Событие создано" in result.text
    assert result.debug.get("calendar_backend") == "local"


def test_calendar_tool_creates_event_with_caldav_backend(tmp_path, monkeypatch) -> None:
    """Test that CalDAV backend works when CALENDAR_BACKEND=caldav"""
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "pass")

    async def fake_create_event_backend(*args, **kwargs) -> calendar_backend.CalendarCreateResult:
        return calendar_backend.CalendarCreateResult(
            uid="evt-1",
            backend="caldav",
            calendar_name="Personal",
            url_base="https://caldav.example.com",
        )

    monkeypatch.setattr(
        "app.core.calendar_backend.CalDAVCalendarBackend.create_event",
        fake_create_event_backend,
    )

    result = asyncio.run(
        create_event(
            start_at=datetime(2026, 2, 5, 18, 30, tzinfo=calendar_store.BOT_TZ),
            title="Врач",
            chat_id=10,
            user_id=1,
            request_id="req-2",
            intent="utility_calendar.add",
        )
    )

    assert result.status == "ok"
    assert "Событие создано" in result.text
    assert result.debug.get("calendar_backend") == "caldav"
    assert result.debug.get("caldav_uid") == "evt-1"
    assert result.debug.get("caldav_calendar") == "Personal"


def test_calendar_tool_fallback_to_local_on_caldav_error(tmp_path, monkeypatch) -> None:
    """Test that CalDAV falls back to local on error"""
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "pass")

    async def fake_create_event_error(*args, **kwargs):
        raise RuntimeError("CalDAV connection failed")

    monkeypatch.setattr(
        "app.core.calendar_backend.CalDAVCalendarBackend.create_event",
        fake_create_event_error,
    )

    result = asyncio.run(
        create_event(
            start_at=datetime(2026, 2, 5, 18, 30, tzinfo=calendar_store.BOT_TZ),
            title="Встреча",
            chat_id=10,
            user_id=1,
            request_id="req-3",
            intent="utility_calendar.add",
        )
    )

    assert result.status == "ok"
    assert "локально" in result.text.lower()
    assert result.debug.get("calendar_backend") == "local_fallback"
    assert result.debug.get("caldav_error") == "RuntimeError"


def test_calendar_tool_lists_events_from_local(tmp_path, monkeypatch) -> None:
    """Test listing events from local backend"""
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "local")

    # Create an event first
    asyncio.run(
        create_event(
            start_at=datetime(2026, 2, 5, 18, 30, tzinfo=calendar_store.BOT_TZ),
            title="Врач",
            chat_id=10,
            user_id=1,
        )
    )

    result = asyncio.run(
        list_calendar_items(
            datetime(2026, 2, 5, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 2, 6, 0, 0, tzinfo=timezone.utc),
            user_id=1,
            chat_id=10,
        )
    )

    assert result.status == "ok"
    assert "Врач" in result.text


def test_calendar_tool_secret_not_in_debug(tmp_path, monkeypatch) -> None:
    """Test that passwords are never leaked in debug info"""
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
    monkeypatch.setenv("CALDAV_URL", "https://user:SECRET_PASS@caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "SECRET_PASS")

    async def fake_create_event_backend(*args, **kwargs) -> calendar_backend.CalendarCreateResult:
        return calendar_backend.CalendarCreateResult(
            uid="evt-1",
            backend="caldav",
            calendar_name="Personal",
            url_base="https://caldav.example.com",
        )

    monkeypatch.setattr(
        "app.core.calendar_backend.CalDAVCalendarBackend.create_event",
        fake_create_event_backend,
    )

    result = asyncio.run(
        create_event(
            start_at=datetime(2026, 2, 5, 18, 30, tzinfo=calendar_store.BOT_TZ),
            title="Тест",
            chat_id=10,
            user_id=1,
        )
    )

    # Check that password is not in text or debug
    assert "SECRET_PASS" not in result.text
    assert "SECRET_PASS" not in str(result.debug)
