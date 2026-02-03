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
