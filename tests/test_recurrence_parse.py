from __future__ import annotations

from datetime import date, datetime

from app.core import calendar_store
from app.core.recurrence_parse import parse_recurrence


def test_parse_weekdays() -> None:
    start = datetime(2026, 3, 1, 9, 0, tzinfo=calendar_store.BOT_TZ)
    result = parse_recurrence("каждый будний день", start, calendar_store.BOT_TZ)
    assert result is not None
    assert "FREQ=WEEKLY" in result.rrule
    assert "BYDAY=MO,TU,WE,TH,FR" in result.rrule


def test_parse_weekly_day() -> None:
    start = datetime(2026, 3, 1, 9, 0, tzinfo=calendar_store.BOT_TZ)
    result = parse_recurrence("каждую пятницу", start, calendar_store.BOT_TZ)
    assert result is not None
    assert "BYDAY=FR" in result.rrule


def test_parse_interval_weekly() -> None:
    start = datetime(2026, 3, 1, 9, 0, tzinfo=calendar_store.BOT_TZ)
    result = parse_recurrence("каждые 2 недели по средам", start, calendar_store.BOT_TZ)
    assert result is not None
    assert "INTERVAL=2" in result.rrule
    assert "BYDAY=WE" in result.rrule


def test_parse_monthly_day() -> None:
    start = datetime(2026, 3, 1, 9, 0, tzinfo=calendar_store.BOT_TZ)
    result = parse_recurrence("каждый месяц 15-го", start, calendar_store.BOT_TZ)
    assert result is not None
    assert "FREQ=MONTHLY" in result.rrule
    assert "BYMONTHDAY=15" in result.rrule


def test_parse_until() -> None:
    start = datetime(2026, 3, 1, 9, 0, tzinfo=calendar_store.BOT_TZ)
    result = parse_recurrence("каждый день до 31.03.2026", start, calendar_store.BOT_TZ)
    assert result is not None
    assert "UNTIL=20260331T" in result.rrule


def test_parse_count() -> None:
    start = datetime(2026, 3, 1, 9, 0, tzinfo=calendar_store.BOT_TZ)
    result = parse_recurrence("каждый день 10 раз", start, calendar_store.BOT_TZ)
    assert result is not None
    assert "COUNT=10" in result.rrule


def test_parse_exdate() -> None:
    start = datetime(2026, 3, 1, 9, 0, tzinfo=calendar_store.BOT_TZ)
    result = parse_recurrence("каждый день кроме 08.03.2026", start, calendar_store.BOT_TZ)
    assert result is not None
    assert result.exdates
    assert result.exdates[0].date() == date(2026, 3, 8)
