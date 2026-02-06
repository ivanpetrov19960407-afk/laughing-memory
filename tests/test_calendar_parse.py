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
