from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from app.core import calendar_store
from app.core.recurrence_scope import RecurrenceScope
from app.core.tools_calendar import update_event


def test_update_event_this_scope_creates_exception(tmp_path, monkeypatch) -> None:
    path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(path))
    start_at = datetime(2026, 3, 1, 9, 0, tzinfo=calendar_store.BOT_TZ)
    created = asyncio.run(
        calendar_store.add_item(
            dt=start_at,
            title="Йога",
            chat_id=1,
            user_id=1,
            reminders_enabled=False,
            rrule="FREQ=DAILY",
        )
    )
    event_payload = created["event"]
    event_id = event_payload["event_id"]
    new_dt = start_at + timedelta(hours=2)
    asyncio.run(
        update_event(
            event_id,
            {"start_at": new_dt},
            scope=RecurrenceScope.THIS,
            instance_dt=start_at,
            user_id=1,
            chat_id=1,
        )
    )
    master = asyncio.run(calendar_store.get_event(event_id))
    assert master is not None
    assert master.exdates is not None
    assert any(item.isoformat() == start_at.isoformat() for item in master.exdates)
    items = asyncio.run(calendar_store.list_items(None, None))
    assert len(items) == 2
    single = next(item for item in items if item.id != event_id)
    assert single.dt == new_dt
    assert single.rrule is None


def test_update_event_all_scope_updates_master(tmp_path, monkeypatch) -> None:
    path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(path))
    start_at = datetime(2026, 5, 1, 10, 0, tzinfo=calendar_store.BOT_TZ)
    created = asyncio.run(
        calendar_store.add_item(
            dt=start_at,
            title="Пилатес",
            chat_id=1,
            user_id=1,
            reminders_enabled=False,
            rrule="FREQ=WEEKLY;BYDAY=FR",
        )
    )
    event_id = created["event"]["event_id"]
    new_dt = start_at + timedelta(hours=1)
    asyncio.run(
        update_event(
            event_id,
            {"start_at": new_dt, "title": "Пилатес+тест"},
            scope=RecurrenceScope.ALL,
            user_id=1,
            chat_id=1,
        )
    )
    updated = asyncio.run(calendar_store.get_event(event_id))
    assert updated is not None
    assert updated.dt == new_dt
    assert updated.title == "Пилатес+тест"


def test_update_event_future_scope_splits_series(tmp_path, monkeypatch) -> None:
    path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(path))
    start_at = datetime(2026, 6, 1, 8, 0, tzinfo=calendar_store.BOT_TZ)
    created = asyncio.run(
        calendar_store.add_item(
            dt=start_at,
            title="Звонок",
            chat_id=1,
            user_id=1,
            reminders_enabled=False,
            rrule="FREQ=DAILY",
        )
    )
    event_id = created["event"]["event_id"]
    instance_dt = start_at + timedelta(days=7)
    new_dt = instance_dt + timedelta(hours=1)
    asyncio.run(
        update_event(
            event_id,
            {"start_at": new_dt, "title": "Звонок (новый)"},
            scope=RecurrenceScope.FUTURE,
            instance_dt=instance_dt,
            user_id=1,
            chat_id=1,
        )
    )
    master = asyncio.run(calendar_store.get_event(event_id))
    assert master is not None
    assert master.rrule is not None
    assert "UNTIL=" in master.rrule
    items = asyncio.run(calendar_store.list_items(None, None))
    future_series = next(item for item in items if item.id != event_id and item.rrule)
    assert future_series.dt == new_dt
    assert future_series.rrule is not None
    assert "UNTIL=" not in future_series.rrule
