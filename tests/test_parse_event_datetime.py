"""Tests for parse_event_datetime and extended parse_user_datetime (Stage 4)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.core import calendar_store


BOT_TZ = calendar_store.BOT_TZ


def _now(year=2026, month=2, day=7, hour=10, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=BOT_TZ)


# ---------------------------------------------------------------------------
# parse_event_datetime: strict formats (backward compat)
# ---------------------------------------------------------------------------

class TestParseEventDatetimeStrictFormats:
    """Existing strict formats should still work."""

    def test_yyyy_mm_dd_hh_mm_only(self):
        dt, rest = calendar_store.parse_event_datetime("2026-02-05 18:30", now=_now())
        assert dt.tzinfo == BOT_TZ
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 2, 5, 18, 30)
        assert rest == ""

    def test_dd_mm_yyyy_hh_mm_only(self):
        dt, rest = calendar_store.parse_event_datetime("05.02.2026 18:30", now=_now())
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 2, 5, 18, 30)
        assert rest == ""

    def test_strict_with_title(self):
        dt, rest = calendar_store.parse_event_datetime("2026-02-05 18:30 Стоматолог", now=_now())
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 2, 5, 18, 30)
        assert rest == "Стоматолог"

    def test_dd_mm_yyyy_with_title(self):
        dt, rest = calendar_store.parse_event_datetime("05.02.2026 18:30 Обед с коллегами", now=_now())
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 2, 5, 18, 30)
        assert rest == "Обед с коллегами"


# ---------------------------------------------------------------------------
# parse_event_datetime: "завтра HH:MM <title>"
# ---------------------------------------------------------------------------

class TestParseEventDatetimeTomorrow:
    def test_tomorrow_time_title(self):
        now = _now()
        dt, rest = calendar_store.parse_event_datetime("завтра 19:00 врач", now=now)
        assert dt.tzinfo == BOT_TZ
        expected_date = now.date() + timedelta(days=1)
        assert dt.date() == expected_date
        assert dt.hour == 19
        assert dt.minute == 0
        assert rest == "врач"

    def test_tomorrow_time_only(self):
        now = _now()
        dt, rest = calendar_store.parse_event_datetime("завтра 19:00", now=now)
        assert dt.date() == now.date() + timedelta(days=1)
        assert dt.hour == 19
        assert rest == ""

    def test_tomorrow_no_time_raises(self):
        with pytest.raises(ValueError):
            calendar_store.parse_event_datetime("завтра", now=_now())


# ---------------------------------------------------------------------------
# parse_event_datetime: "сегодня HH:MM <title>"
# ---------------------------------------------------------------------------

class TestParseEventDatetimeToday:
    def test_today_time_title(self):
        now = _now()
        dt, rest = calendar_store.parse_event_datetime("сегодня 18:30 созвон", now=now)
        assert dt.date() == now.date()
        assert dt.hour == 18
        assert dt.minute == 30
        assert rest == "созвон"


# ---------------------------------------------------------------------------
# parse_event_datetime: "послезавтра"
# ---------------------------------------------------------------------------

class TestParseEventDatetimeDayAfterTomorrow:
    def test_day_after_tomorrow_time_title(self):
        now = _now()
        dt, rest = calendar_store.parse_event_datetime("послезавтра 14:00 обед", now=now)
        expected_date = now.date() + timedelta(days=2)
        assert dt.date() == expected_date
        assert dt.hour == 14
        assert rest == "обед"


# ---------------------------------------------------------------------------
# parse_event_datetime: "через N минут/часов <title>"
# ---------------------------------------------------------------------------

class TestParseEventDatetimeRelative:
    def test_through_10_minutes_title(self):
        now = _now(hour=10, minute=0)
        dt, rest = calendar_store.parse_event_datetime("через 10 минут созвон", now=now)
        assert dt.tzinfo == BOT_TZ
        assert dt.hour == 10
        assert dt.minute == 10
        assert rest == "созвон"

    def test_through_2_hours_title(self):
        now = _now(hour=10, minute=0)
        dt, rest = calendar_store.parse_event_datetime("через 2 часа тренировка", now=now)
        assert dt.hour == 12
        assert dt.minute == 0
        assert rest == "тренировка"

    def test_through_hours_and_minutes(self):
        now = _now(hour=10, minute=0)
        dt, rest = calendar_store.parse_event_datetime("через 1 час 30 минут встреча", now=now)
        assert dt.hour == 11
        assert dt.minute == 30
        assert rest == "встреча"


# ---------------------------------------------------------------------------
# parse_event_datetime: DD.MM HH:MM <title>
# ---------------------------------------------------------------------------

class TestParseEventDatetimeDayMonth:
    def test_day_month_time_title(self):
        now = _now()
        dt, rest = calendar_store.parse_event_datetime("07.02 12:00 стоматолог", now=now)
        assert dt.tzinfo == BOT_TZ
        assert dt.month == 2
        assert dt.day == 7
        assert dt.hour == 12
        assert dt.minute == 0
        assert rest == "стоматолог"

    def test_day_month_time_only(self):
        now = _now()
        dt, rest = calendar_store.parse_event_datetime("07.02 12:00", now=now)
        assert dt.month == 2
        assert dt.day == 7
        assert rest == ""


# ---------------------------------------------------------------------------
# parse_event_datetime: weekday patterns
# ---------------------------------------------------------------------------

class TestParseEventDatetimeWeekday:
    def test_v_pyatnitsu_time_title(self):
        # 2026-02-07 is Saturday, so Friday = 6 days ahead
        now = _now(year=2026, month=2, day=7)  # Saturday
        dt, rest = calendar_store.parse_event_datetime("в пятницу 10:15 встреча", now=now)
        assert dt.tzinfo == BOT_TZ
        assert dt.weekday() == 4  # Friday
        assert dt.hour == 10
        assert dt.minute == 15
        assert rest == "встреча"
        # Should be next Friday (2026-02-13)
        assert dt.date() == now.date() + timedelta(days=6)

    def test_weekday_short_form(self):
        now = _now(year=2026, month=2, day=7)  # Saturday
        dt, rest = calendar_store.parse_event_datetime("в пт 10:15 встреча", now=now)
        assert dt.weekday() == 4
        assert rest == "встреча"

    def test_weekday_ponedelnik(self):
        now = _now(year=2026, month=2, day=7)  # Saturday
        dt, rest = calendar_store.parse_event_datetime("в понедельник 9:00 стенд-ап", now=now)
        assert dt.weekday() == 0  # Monday
        assert dt.hour == 9
        assert rest == "стенд-ап"
        # Saturday -> Monday = 2 days
        assert dt.date() == now.date() + timedelta(days=2)


# ---------------------------------------------------------------------------
# parse_event_datetime: "N вечера" time modifier
# ---------------------------------------------------------------------------

class TestParseEventDatetimeEvening:
    def test_today_7_vechera_title(self):
        now = _now()
        dt, rest = calendar_store.parse_event_datetime("сегодня 7 вечера кино", now=now)
        assert dt.hour == 19
        assert dt.minute == 0
        assert rest == "кино"

    def test_tomorrow_8_vechera(self):
        now = _now()
        dt, rest = calendar_store.parse_event_datetime("завтра 8 вечера ужин", now=now)
        assert dt.hour == 20
        assert rest == "ужин"


# ---------------------------------------------------------------------------
# parse_event_datetime: timezone awareness
# ---------------------------------------------------------------------------

class TestParseEventDatetimeTimezone:
    def test_all_results_are_tz_aware(self):
        now = _now()
        cases = [
            "2026-02-05 18:30",
            "завтра 19:00 врач",
            "через 10 минут созвон",
            "07.02 12:00 стоматолог",
            "в пятницу 10:15 встреча",
            "сегодня 7 вечера кино",
        ]
        for case in cases:
            dt, _ = calendar_store.parse_event_datetime(case, now=now)
            assert dt.tzinfo is not None, f"Naive datetime for input: {case}"
            assert dt.tzinfo == BOT_TZ, f"Wrong timezone for input: {case}"


# ---------------------------------------------------------------------------
# parse_event_datetime: error cases
# ---------------------------------------------------------------------------

class TestParseEventDatetimeErrors:
    def test_empty_raises(self):
        with pytest.raises(ValueError):
            calendar_store.parse_event_datetime("", now=_now())

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            calendar_store.parse_event_datetime("абракадабра", now=_now())

    def test_tomorrow_no_time_raises(self):
        with pytest.raises(ValueError):
            calendar_store.parse_event_datetime("завтра", now=_now())

    def test_only_title_raises(self):
        with pytest.raises(ValueError):
            calendar_store.parse_event_datetime("врач", now=_now())


# ---------------------------------------------------------------------------
# parse_user_datetime: extended weekday support
# ---------------------------------------------------------------------------

class TestParseUserDatetimeWeekdays:
    def test_v_pyatnitsu(self):
        now = _now(year=2026, month=2, day=7)  # Saturday
        dt = calendar_store.parse_user_datetime("в пятницу 10:15", now=now)
        assert dt.weekday() == 4
        assert dt.hour == 10
        assert dt.minute == 15

    def test_weekday_same_day_future(self):
        # If today is Saturday and we say "в субботу 23:00" and it's 10:00 now,
        # it should be today
        now = _now(year=2026, month=2, day=7, hour=10)  # Saturday 10:00
        dt = calendar_store.parse_user_datetime("в субботу 23:00", now=now)
        assert dt.date() == now.date()  # same day, later time

    def test_weekday_same_day_past_next_week(self):
        # If today is Saturday 10:00 and we say "в субботу 9:00",
        # time already passed, so next week
        now = _now(year=2026, month=2, day=7, hour=10)  # Saturday 10:00
        dt = calendar_store.parse_user_datetime("в субботу 9:00", now=now)
        assert dt.date() == now.date() + timedelta(days=7)

    def test_poslezavtra(self):
        now = _now()
        dt = calendar_store.parse_user_datetime("послезавтра 14:00", now=now)
        assert dt.date() == now.date() + timedelta(days=2)
        assert dt.hour == 14

    def test_7_vechera(self):
        now = _now()
        dt = calendar_store.parse_user_datetime("сегодня 7 вечера", now=now)
        assert dt.hour == 19


# ---------------------------------------------------------------------------
# parse_user_datetime: backward compatibility
# ---------------------------------------------------------------------------

class TestParseUserDatetimeBackwardCompat:
    def test_relative_minutes(self):
        base = calendar_store.parse_user_datetime("2026-02-05 10:00")
        parsed = calendar_store.parse_user_datetime("через 10 минут", now=base)
        assert parsed.hour == 10
        assert parsed.minute == 10

    def test_day_month_format(self):
        base = calendar_store.parse_user_datetime("2026-02-01 10:00")
        parsed = calendar_store.parse_user_datetime("07.02 12:00", now=base)
        assert parsed.month == 2
        assert parsed.day == 7

    def test_strict_format(self):
        dt = calendar_store.parse_user_datetime("2026-02-05 18:30")
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 2, 5, 18, 30)
