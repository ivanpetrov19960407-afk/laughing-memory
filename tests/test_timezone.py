"""Tests for timezone configuration and naive datetime prevention."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core import calendar_store


def test_default_timezone_is_vilnius() -> None:
    """BOT_TZ should default to Europe/Vilnius."""
    assert calendar_store.BOT_TZ == ZoneInfo("Europe/Vilnius")


def test_parse_local_datetime_always_aware() -> None:
    """parse_local_datetime must always return an aware datetime."""
    dt = calendar_store.parse_local_datetime("2026-03-15 14:00")
    assert dt.tzinfo is not None
    assert dt.tzinfo == calendar_store.BOT_TZ


def test_parse_user_datetime_always_aware() -> None:
    dt = calendar_store.parse_user_datetime("2026-03-15 14:00")
    assert dt.tzinfo is not None


def test_day_bounds_are_aware() -> None:
    from datetime import date
    start, end = calendar_store.day_bounds(date(2026, 3, 15))
    assert start.tzinfo is not None
    assert end.tzinfo is not None


def test_week_bounds_are_aware() -> None:
    from datetime import date
    start, end = calendar_store.week_bounds(date(2026, 3, 15))
    assert start.tzinfo is not None
    assert end.tzinfo is not None


def test_backward_compatible_aliases() -> None:
    """MOSCOW_TZ and VILNIUS_TZ should be same as BOT_TZ."""
    assert calendar_store.MOSCOW_TZ == calendar_store.BOT_TZ
    assert calendar_store.VILNIUS_TZ == calendar_store.BOT_TZ
    assert calendar_store.VIENNA_TZ == calendar_store.BOT_TZ
