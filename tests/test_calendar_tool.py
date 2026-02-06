from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.core import calendar_store
from app.core.tools_calendar import create_event, list_calendar_items


def test_calendar_tool_defaults_to_local_backend(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
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
    assert result.debug.get("calendar_backend") == "local"
    assert isinstance(result.debug.get("event_id"), str)


def test_calendar_tool_creates_event_with_caldav_backend(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "pass")

    from app.core.calendar_backend import CalendarCreateResult

    async def fake_backend_create_event(self, **kwargs) -> CalendarCreateResult:
        return CalendarCreateResult(
            uid="evt-1",
            debug={
                "calendar_backend": "caldav",
                "caldav_calendar": "Personal",
                "caldav_uid": "evt-1",
                "caldav_url_base": "https://caldav.example.com",
            },
        )

    monkeypatch.setattr("app.core.tools_calendar.CalDAVCalendarBackend.create_event", fake_backend_create_event)

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
    store = calendar_store.load_store()
    events = store.get("events") or []
    assert any(event.get("event_id") == "evt-1" for event in events)


def test_calendar_tool_lists_events_from_local_store(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "local")
    calendar_store.save_store_atomic(
        {
            "events": [
                {
                    "event_id": "evt-2",
                    "dt_start": datetime(2026, 2, 5, 18, 30, tzinfo=timezone.utc).isoformat(),
                    "text": "Врач",
                    "created_at": datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc).isoformat(),
                    "chat_id": 10,
                    "user_id": 1,
                }
            ],
            "reminders": [],
            "updated_at": datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc).isoformat(),
        }
    )

    result = asyncio.run(
        list_calendar_items(
            datetime(2026, 2, 5, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 2, 6, 0, 0, tzinfo=timezone.utc),
            user_id=1,
        )
    )

    assert result.status == "ok"
    assert "evt-2" in result.text


def test_calendar_tool_caldav_error_falls_back_to_local_and_does_not_leak_secrets(tmp_path, monkeypatch, caplog) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "super-secret-password")

    class ExplodingError(RuntimeError):
        def __str__(self) -> str:  # pragma: no cover - used for leak detection
            return "boom super-secret-password"

    async def fake_backend_create_event(self, **kwargs):
        raise ExplodingError("boom super-secret-password")

    monkeypatch.setattr("app.core.tools_calendar.CalDAVCalendarBackend.create_event", fake_backend_create_event)

    result = asyncio.run(
        create_event(
            start_at=datetime(2026, 2, 5, 18, 30, tzinfo=calendar_store.BOT_TZ),
            title="Врач",
            chat_id=10,
            user_id=1,
            request_id="req-3",
            intent="utility_calendar.add",
        )
    )

    assert result.status == "ok"
    assert result.debug.get("calendar_backend") == "local_fallback"
    assert "caldav_error" in result.debug
    assert "super-secret-password" not in result.text
    assert "super-secret-password" not in str(result.debug)
    assert "super-secret-password" not in caplog.text
