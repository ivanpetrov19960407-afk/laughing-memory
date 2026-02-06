from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.core import calendar_store
from app.core import tools_calendar_caldav
from app.core.tools_calendar import create_event, list_calendar_items


def test_calendar_tool_uses_local_when_not_connected(tmp_path, monkeypatch) -> None:
    """When CalDAV is not configured, local backend is used (default)."""
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.delenv("CALDAV_URL", raising=False)
    monkeypatch.delenv("CALDAV_USERNAME", raising=False)
    monkeypatch.delenv("CALDAV_PASSWORD", raising=False)
    monkeypatch.delenv("CALENDAR_BACKEND", raising=False)

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


def test_calendar_tool_creates_event_with_caldav(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "pass")

    async def fake_create_event(*args, **kwargs) -> tools_calendar_caldav.CreatedEvent:
        return tools_calendar_caldav.CreatedEvent(uid="evt-1", href="https://caldav.example.com/e/1")

    monkeypatch.setattr("app.core.tools_calendar_caldav.create_event", fake_create_event)

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
    store = calendar_store.load_store()
    events = store.get("events") or []
    assert any(event.get("event_id") == "evt-1" for event in events)


def test_calendar_tool_lists_events(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "pass")

    async def fake_list_events(*args, **kwargs) -> list[tools_calendar_caldav.CalDAVEvent]:
        return [
            tools_calendar_caldav.CalDAVEvent(
                uid="evt-2",
                summary="Врач",
                start_at=datetime(2026, 2, 5, 18, 30, tzinfo=timezone.utc),
            )
        ]

    monkeypatch.setattr("app.core.tools_calendar_caldav.list_events", fake_list_events)

    result = asyncio.run(
        list_calendar_items(
            datetime(2026, 2, 5, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 2, 6, 0, 0, tzinfo=timezone.utc),
            user_id=1,
        )
    )

    assert result.status == "ok"
    assert "evt-2" in result.text
