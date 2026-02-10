"""Тесты формирования дайджеста дня (Stage 5)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core import calendar_store
from app.core.digest import build_daily_digest_text


def test_build_daily_digest_empty_returns_empty_string() -> None:
    """Если событий и напоминаний нет — не слать сообщение."""
    tz = ZoneInfo("Europe/Moscow")
    text = build_daily_digest_text([], [], for_date=date(2026, 2, 10), tz=tz)
    assert text == ""


def test_build_daily_digest_with_events_only() -> None:
    tz = ZoneInfo("Europe/Moscow")
    day_start = datetime(2026, 2, 10, 0, 0, tzinfo=tz)
    events = [
        calendar_store.CalendarItem(
            id="e1",
            ts=day_start.isoformat(),
            title="Встреча",
            created_at=day_start.isoformat(),
            dt=day_start.replace(hour=10, minute=0),
            chat_id=1,
            user_id=1,
        ),
        calendar_store.CalendarItem(
            id="e2",
            ts=day_start.isoformat(),
            title="Созвон",
            created_at=day_start.isoformat(),
            dt=day_start.replace(hour=14, minute=30),
            chat_id=1,
            user_id=1,
        ),
    ]
    text = build_daily_digest_text(events, [], for_date=date(2026, 2, 10), tz=tz)
    assert "Дайджест" in text
    assert "События:" in text
    assert "10:00" in text and "Встреча" in text
    assert "14:30" in text and "Созвон" in text
    assert "Напоминания:" not in text


def test_build_daily_digest_with_reminders_only() -> None:
    tz = ZoneInfo("Europe/Moscow")
    day_start = datetime(2026, 2, 10, 0, 0, tzinfo=tz)
    reminders = [
        calendar_store.ReminderItem(
            id="r1",
            event_id="e1",
            user_id=1,
            chat_id=1,
            trigger_at=day_start.replace(hour=9, minute=0),
            text="Позвонить",
            enabled=True,
            sent_at=None,
            status="active",
            recurrence=None,
            last_triggered_at=None,
        ),
    ]
    text = build_daily_digest_text([], reminders, for_date=date(2026, 2, 10), tz=tz)
    assert "Дайджест" in text
    assert "Напоминания:" in text
    assert "09:00" in text and "Позвонить" in text


def test_build_daily_digest_respects_max_items() -> None:
    tz = ZoneInfo("Europe/Moscow")
    day_start = datetime(2026, 2, 10, 0, 0, tzinfo=tz)
    events = [
        calendar_store.CalendarItem(
            id=f"e{i}",
            ts=day_start.isoformat(),
            title=f"Event {i}",
            created_at=day_start.isoformat(),
            dt=day_start.replace(hour=i % 24, minute=0),
            chat_id=1,
            user_id=1,
        )
        for i in range(25)
    ]
    text = build_daily_digest_text(
        events, [], for_date=date(2026, 2, 10), tz=tz, max_events=5
    )
    assert "Event 0" in text
    assert "Event 3" in text
    assert text.count("•") == 5
