from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core import tools_calendar_caldav


class FakeEvent:
    def __init__(self, data: str | None = None, url: str | None = None) -> None:
        self.data = data
        self.url = url

    def delete(self) -> None:
        return None


class FakeCalendar:
    def __init__(self, name: str) -> None:
        self.name = name
        self.added: str | None = None
        self._events: list[FakeEvent] = []

    def add_event(self, ical: str) -> FakeEvent:
        self.added = ical
        return FakeEvent(url="https://caldav.example.com/event/1.ics")

    def date_search(self, start, end):
        return list(self._events)

    def events(self):
        return list(self._events)


class FakePrincipal:
    def __init__(self, calendars: list[FakeCalendar]) -> None:
        self._calendars = calendars

    def calendars(self):
        return list(self._calendars)


class FakeClient:
    def __init__(self, calendars: list[FakeCalendar]) -> None:
        self._principal = FakePrincipal(calendars)

    def principal(self):
        return self._principal


def _event_payload(uid: str, summary: str, dtstart: str) -> str:
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTART:{dtstart}\r\n"
        f"SUMMARY:{summary}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )


def test_load_caldav_config_missing(monkeypatch) -> None:
    monkeypatch.delenv("CALDAV_URL", raising=False)
    monkeypatch.delenv("CALDAV_USERNAME", raising=False)
    monkeypatch.delenv("CALDAV_PASSWORD", raising=False)
    assert tools_calendar_caldav.load_caldav_config() is None


def test_create_event_uses_named_calendar(monkeypatch) -> None:
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "pass")
    monkeypatch.setenv("CALDAV_CALENDAR_NAME", "Work")
    personal = FakeCalendar("Personal")
    work = FakeCalendar("Work")
    fake_client = FakeClient([personal, work])

    monkeypatch.setattr("app.core.tools_calendar_caldav.caldav.DAVClient", lambda *args, **kwargs: fake_client)

    config = tools_calendar_caldav.load_caldav_config()
    created = asyncio.run(
        tools_calendar_caldav.create_event(
            config,
            start_at=datetime(2026, 2, 5, 18, 30, tzinfo=timezone.utc),
            title="Standup",
            description="Daily",
        )
    )

    assert created.uid
    assert created.href == "https://caldav.example.com/event/1.ics"
    assert work.added is not None
    assert personal.added is None
    assert "SUMMARY:Standup" in work.added


def test_list_events_parses_items(monkeypatch) -> None:
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "pass")
    calendar = FakeCalendar("Default")
    calendar._events = [
        FakeEvent(
            data=_event_payload("evt-1", "Врач", "20260205T180000Z"),
            url="https://caldav.example.com/event/evt-1.ics",
        )
    ]
    fake_client = FakeClient([calendar])
    monkeypatch.setattr("app.core.tools_calendar_caldav.caldav.DAVClient", lambda *args, **kwargs: fake_client)

    config = tools_calendar_caldav.load_caldav_config()
    items = asyncio.run(
        tools_calendar_caldav.list_events(
            config,
            start=datetime(2026, 2, 5, 0, 0, tzinfo=timezone.utc),
            end=datetime(2026, 2, 6, 0, 0, tzinfo=timezone.utc),
            limit=10,
        )
    )

    assert len(items) == 1
    assert items[0].uid == "evt-1"
    assert items[0].summary == "Врач"


def test_check_connection_returns_calendar_name(monkeypatch) -> None:
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "pass")
    calendar = FakeCalendar("Primary")
    fake_client = FakeClient([calendar])
    monkeypatch.setattr("app.core.tools_calendar_caldav.caldav.DAVClient", lambda *args, **kwargs: fake_client)

    config = tools_calendar_caldav.load_caldav_config()
    ok_status, name = asyncio.run(tools_calendar_caldav.check_connection(config))

    assert ok_status is True
    assert name == "Primary"
