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


def test_parse_user_datetime_poslezavtra() -> None:
    base = calendar_store.parse_user_datetime("2026-02-05 10:00")
    parsed = calendar_store.parse_user_datetime("послезавтра 14:30", now=base)
    assert parsed.year == 2026
    assert parsed.month == 2
    assert parsed.day == 7
    assert parsed.hour == 14
    assert parsed.minute == 30


def test_parse_user_datetime_evening_time() -> None:
    base = calendar_store.parse_user_datetime("2026-02-05 10:00")
    parsed = calendar_store.parse_user_datetime("сегодня в 7 вечера", now=base)
    assert parsed.year == 2026
    assert parsed.month == 2
    assert parsed.day == 5
    assert parsed.hour == 19
    assert parsed.minute == 0


def test_parse_user_datetime_weekday_friday() -> None:
    # Base is Wednesday 2026-02-04
    base = calendar_store.parse_user_datetime("2026-02-04 10:00")
    parsed = calendar_store.parse_user_datetime("в пятницу 10:15", now=base)
    assert parsed.year == 2026
    assert parsed.month == 2
    assert parsed.day == 6  # Friday
    assert parsed.hour == 10
    assert parsed.minute == 15


def test_parse_user_datetime_weekday_monday_next_week() -> None:
    # Base is Wednesday 2026-02-04, asking for Monday should give next week
    base = calendar_store.parse_user_datetime("2026-02-04 10:00")
    parsed = calendar_store.parse_user_datetime("в понедельник 09:00", now=base)
    assert parsed.year == 2026
    assert parsed.month == 2
    assert parsed.day == 9  # Next Monday
    assert parsed.hour == 9
    assert parsed.minute == 0


def test_parse_user_datetime_weekday_short_form() -> None:
    # Base is Monday 2026-02-02
    base = calendar_store.parse_user_datetime("2026-02-02 10:00")
    parsed = calendar_store.parse_user_datetime("пт 15:30", now=base)
    assert parsed.year == 2026
    assert parsed.month == 2
    assert parsed.day == 6  # Friday
    assert parsed.hour == 15
    assert parsed.minute == 30


def test_parse_event_datetime_tomorrow_with_title() -> None:
    base = calendar_store.parse_user_datetime("2026-02-05 10:00")
    dt, title = calendar_store.parse_event_datetime("завтра 19:00 врач", now=base)
    assert dt.year == 2026
    assert dt.month == 2
    assert dt.day == 6
    assert dt.hour == 19
    assert dt.minute == 0
    assert title == "врач"


def test_parse_event_datetime_today_with_title() -> None:
    base = calendar_store.parse_user_datetime("2026-02-05 10:00")
    dt, title = calendar_store.parse_event_datetime("сегодня 18:30 созвон", now=base)
    assert dt.year == 2026
    assert dt.month == 2
    assert dt.day == 5
    assert dt.hour == 18
    assert dt.minute == 30
    assert title == "созвон"


def test_parse_event_datetime_weekday_with_title() -> None:
    # Base is Wednesday 2026-02-04
    base = calendar_store.parse_user_datetime("2026-02-04 10:00")
    dt, title = calendar_store.parse_event_datetime("в пятницу 10:15 встреча", now=base)
    assert dt.year == 2026
    assert dt.month == 2
    assert dt.day == 6  # Friday
    assert dt.hour == 10
    assert dt.minute == 15
    assert title == "встреча"


def test_parse_event_datetime_day_month_with_title() -> None:
    base = calendar_store.parse_user_datetime("2026-02-01 10:00")
    dt, title = calendar_store.parse_event_datetime("07.02 12:00 стоматолог", now=base)
    assert dt.year == 2026
    assert dt.month == 2
    assert dt.day == 7
    assert dt.hour == 12
    assert dt.minute == 0
    assert title == "стоматолог"


def test_parse_event_datetime_relative_with_title() -> None:
    base = calendar_store.parse_user_datetime("2026-02-05 10:00")
    dt, title = calendar_store.parse_event_datetime("через 2 часа тренировка", now=base)
    assert dt.year == 2026
    assert dt.month == 2
    assert dt.day == 5
    assert dt.hour == 12
    assert dt.minute == 0
    assert title == "тренировка"


def test_parse_event_datetime_evening_with_title() -> None:
    base = calendar_store.parse_user_datetime("2026-02-05 10:00")
    dt, title = calendar_store.parse_event_datetime("сегодня 7 вечера кино", now=base)
    assert dt.year == 2026
    assert dt.month == 2
    assert dt.day == 5
    assert dt.hour == 19
    assert dt.minute == 0
    assert title == "кино"


def test_parse_event_datetime_strict_format_no_title() -> None:
    dt, title = calendar_store.parse_event_datetime("2026-02-05 18:30")
    assert dt.year == 2026
    assert dt.month == 2
    assert dt.day == 5
    assert dt.hour == 18
    assert dt.minute == 30
    assert title == ""


def test_parse_event_datetime_backward_compatibility() -> None:
    # Old format should still work
    dt, title = calendar_store.parse_event_datetime("05.02.2026 18:30")
    assert dt.year == 2026
    assert dt.month == 2
    assert dt.day == 5
    assert dt.hour == 18
    assert dt.minute == 30
    assert title == ""
