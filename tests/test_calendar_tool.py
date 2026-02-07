from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core import calendar_store
from app.core import tools_calendar_caldav
from app.core.tools_calendar import create_event, list_calendar_items, list_reminders


def test_calendar_tool_defaults_to_local_backend(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.delenv("CALENDAR_BACKEND", raising=False)
    monkeypatch.delenv("CALDAV_URL", raising=False)
    monkeypatch.delenv("CALDAV_USERNAME", raising=False)
    monkeypatch.delenv("CALDAV_PASSWORD", raising=False)

    result = asyncio.run(
        create_event(
            start_at=datetime(2026, 2, 5, 18, 30, tzinfo=calendar_store.BOT_TZ),
            title="Врач",
            chat_id=10,
            user_id=1,
            request_id="req-1",
            intent="calendar.add",
        )
    )

    assert result.status == "ok"
    assert "Событие создано" in result.text
    assert result.debug.get("calendar_backend") == "local"


def test_calendar_tool_creates_event_with_caldav(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "pass")

    async def fake_create_event(*args, **kwargs) -> tools_calendar_caldav.CreatedEvent:
        return tools_calendar_caldav.CreatedEvent(
            uid="evt-1",
            href="https://caldav.example.com/e/1",
            calendar_name="Personal",
            calendar_url_base="https://caldav.example.com/remote.php/dav/",
        )

    monkeypatch.setattr("app.core.tools_calendar_caldav.create_event", fake_create_event)

    result = asyncio.run(
        create_event(
            start_at=datetime(2026, 2, 5, 18, 30, tzinfo=calendar_store.BOT_TZ),
            title="Врач",
            chat_id=10,
            user_id=1,
            request_id="req-2",
            intent="calendar.add",
        )
    )

    assert result.status == "ok"
    assert "Событие создано" in result.text
    assert result.debug.get("calendar_backend") == "caldav"
    assert result.debug.get("caldav_uid") == "evt-1"
    assert result.debug.get("caldav_calendar") == "Personal"
    assert result.debug.get("caldav_url_base") == "https://caldav.example.com/remote.php/dav/"
    store = calendar_store.load_store()
    events = store.get("events") or []
    assert any(event.get("event_id") == "evt-1" for event in events)


def test_calendar_tool_passes_rrule_and_exdates_to_caldav(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "pass")

    captured: dict[str, object] = {}

    async def fake_create_event(*args, **kwargs) -> tools_calendar_caldav.CreatedEvent:
        captured.update(kwargs)
        return tools_calendar_caldav.CreatedEvent(
            uid="evt-rrule",
            href="https://caldav.example.com/e/rrule",
            calendar_name="Personal",
            calendar_url_base="https://caldav.example.com/remote.php/dav/",
        )

    monkeypatch.setattr("app.core.tools_calendar_caldav.create_event", fake_create_event)

    result = asyncio.run(
        create_event(
            start_at=datetime(2026, 3, 1, 9, 0, tzinfo=calendar_store.BOT_TZ),
            title="Тренировка",
            recurrence_text="каждый будний день кроме 08.03.2026",
            chat_id=10,
            user_id=1,
            request_id="req-rrule",
            intent="calendar.add",
        )
    )

    assert result.status == "ok"
    assert "Создал повтор" in result.text
    assert "BYDAY=MO,TU,WE,TH,FR" in str(captured.get("rrule"))
    exdates = captured.get("exdates")
    assert isinstance(exdates, list)
    assert exdates[0].date() == datetime(2026, 3, 8, tzinfo=calendar_store.BOT_TZ).date()
    store = calendar_store.load_store()
    events = store.get("events") or []
    stored = next(event for event in events if event.get("event_id") == "evt-rrule")
    assert stored.get("rrule") == captured.get("rrule")
    assert stored.get("exdates")


def test_calendar_tool_lists_events(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
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


def test_list_reminders_includes_delete_actions(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.BOT_TZ)
    asyncio.run(
        calendar_store.add_reminder(
            trigger_at=now + timedelta(minutes=30),
            text="Check",
            chat_id=10,
            user_id=1,
            enabled=True,
        )
    )

    result = asyncio.run(list_reminders(now, limit=5, intent="utility_reminders.list"))

    assert result.status == "ok"
    assert any(action.id == "utility_reminders.delete" for action in result.actions)
    assert any(action.payload.get("op") == "reminder.delete" and action.payload.get("reminder_id") for action in result.actions)


def test_calendar_tool_fallbacks_to_local_on_caldav_error(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "pass")

    async def fake_create_event(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.core.tools_calendar_caldav.create_event", fake_create_event)

    result = asyncio.run(
        create_event(
            start_at=datetime(2026, 2, 5, 18, 30, tzinfo=calendar_store.BOT_TZ),
            title="Врач",
            chat_id=10,
            user_id=1,
            request_id="req-3",
            intent="calendar.add",
        )
    )

    assert result.status == "ok"
    assert result.debug.get("calendar_backend") == "local_fallback"
    assert result.debug.get("caldav_error") == "RuntimeError"
    assert isinstance(result.debug.get("event_id"), str)


def test_calendar_tool_does_not_leak_caldav_password(tmp_path, monkeypatch, caplog) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "secret-pass")

    async def fake_create_event(*args, **kwargs):
        raise RuntimeError("boom secret-pass")

    monkeypatch.setattr("app.core.tools_calendar_caldav.create_event", fake_create_event)

    caplog.set_level(logging.ERROR)
    result = asyncio.run(
        create_event(
            start_at=datetime(2026, 2, 5, 18, 30, tzinfo=calendar_store.BOT_TZ),
            title="Врач",
            chat_id=10,
            user_id=1,
            request_id="req-4",
            intent="calendar.add",
        )
    )

    assert "secret-pass" not in result.text
    assert "secret-pass" not in str(result.debug)
    assert "secret-pass" not in caplog.text
