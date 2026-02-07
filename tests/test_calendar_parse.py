from datetime import datetime, timedelta

import pytest

from app.core import calendar_store


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-02-05 18:30", (2026, 2, 5, 18, 30)),
        ("05.02.2026 18:30", (2026, 2, 5, 18, 30)),
        ("2026.02.05 18:30", (2026, 2, 5, 18, 30)),
        ("05-02-2026 18:30", (2026, 2, 5, 18, 30)),
    ],
)
def test_parse_local_datetime_formats(value: str, expected: tuple[int, int, int, int, int]) -> None:
    parsed = calendar_store.parse_local_datetime(value)

    assert parsed.tzinfo == calendar_store.VIENNA_TZ
    assert (parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute) == expected


def test_parse_local_datetime_error_message() -> None:
    with pytest.raises(ValueError) as exc:
        calendar_store.parse_local_datetime("2026/02/05 18:30")

    assert "YYYY-MM-DD HH:MM" in str(exc.value)
    assert "DD.MM.YYYY HH:MM" in str(exc.value)


def test_parse_user_datetime_relative_minutes() -> None:
    now = calendar_store.VIENNA_TZ
    base = calendar_store.parse_user_datetime("2026-02-05 10:00")
    parsed = calendar_store.parse_user_datetime("через 10 минут", now=base)
    assert parsed.year == 2026
    assert parsed.month == 2
    assert parsed.day == 5
    assert parsed.hour == 10
    assert parsed.minute == 10
    assert parsed.tzinfo == now


def test_parse_user_datetime_day_month() -> None:
    base = calendar_store.parse_user_datetime("2026-02-01 10:00")
    parsed = calendar_store.parse_user_datetime("07.02 12:00", now=base)
    assert parsed.year == 2026
    assert parsed.month == 2
    assert parsed.day == 7
    assert parsed.hour == 12
    assert parsed.minute == 0


@pytest.mark.parametrize(
    ("value", "base_now", "expected_dt", "expected_title"),
    [
        (
            "завтра 19:00 врач",
            datetime(2026, 2, 7, 10, 0, tzinfo=calendar_store.BOT_TZ),
            datetime(2026, 2, 8, 19, 0, tzinfo=calendar_store.BOT_TZ),
            "врач",
        ),
        (
            "через 10 минут созвон",
            datetime(2026, 2, 7, 10, 0, tzinfo=calendar_store.BOT_TZ),
            datetime(2026, 2, 7, 10, 10, tzinfo=calendar_store.BOT_TZ),
            "созвон",
        ),
        (
            "в пятницу 10:15 встреча",
            datetime(2026, 2, 7, 10, 0, tzinfo=calendar_store.BOT_TZ),  # 2026-02-07 is Saturday
            datetime(2026, 2, 13, 10, 15, tzinfo=calendar_store.BOT_TZ),
            "встреча",
        ),
        (
            "07.02 12:00 стоматолог",
            datetime(2026, 2, 1, 10, 0, tzinfo=calendar_store.BOT_TZ),
            datetime(2026, 2, 7, 12, 0, tzinfo=calendar_store.BOT_TZ),
            "стоматолог",
        ),
        (
            "сегодня 7 вечера кино",
            datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.BOT_TZ),
            datetime(2026, 2, 5, 19, 0, tzinfo=calendar_store.BOT_TZ),
            "кино",
        ),
    ],
)
def test_parse_event_datetime_ru(value: str, base_now: datetime, expected_dt: datetime, expected_title: str) -> None:
    dt, title = calendar_store.parse_event_datetime(value, now=base_now, tz=calendar_store.BOT_TZ)
    assert dt.tzinfo == calendar_store.BOT_TZ
    assert dt == expected_dt
    assert title == expected_title


def test_parse_event_datetime_weekday_same_day_if_future() -> None:
    base_now = datetime(2026, 2, 6, 9, 0, tzinfo=calendar_store.BOT_TZ)  # Friday
    dt, title = calendar_store.parse_event_datetime("в пятницу 10:15 встреча", now=base_now, tz=calendar_store.BOT_TZ)
    assert dt == datetime(2026, 2, 6, 10, 15, tzinfo=calendar_store.BOT_TZ)
    assert title == "встреча"


def test_parse_event_datetime_weekday_rolls_if_past_time() -> None:
    base_now = datetime(2026, 2, 6, 11, 0, tzinfo=calendar_store.BOT_TZ)  # Friday, already after 10:15
    dt, title = calendar_store.parse_event_datetime("в пятницу 10:15 встреча", now=base_now, tz=calendar_store.BOT_TZ)
    assert dt == datetime(2026, 2, 13, 10, 15, tzinfo=calendar_store.BOT_TZ)
    assert title == "встреча"


def test_parse_event_datetime_requires_time() -> None:
    base_now = datetime(2026, 2, 7, 10, 0, tzinfo=calendar_store.BOT_TZ)
    with pytest.raises(ValueError) as exc:
        calendar_store.parse_event_datetime("завтра врач", now=base_now, tz=calendar_store.BOT_TZ)
    assert "время" in str(exc.value).lower()
