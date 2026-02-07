from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dataclasses import replace

from app.core.recurrence_series import (
    RecurrenceSeries,
    delete_instance_this,
    delete_series_future,
    edit_instance_this,
    edit_series_future,
)


def _series(tz: ZoneInfo) -> RecurrenceSeries:
    return RecurrenceSeries(
        series_id="series-1",
        start_dt=datetime(2026, 3, 1, 9, 0, tzinfo=tz),
        title="Йога",
        rrule="FREQ=DAILY",
        exdates=[],
        overrides={},
        timezone=tz,
    )


def test_edit_instance_this_moves_time_adds_exdate_and_override() -> None:
    tz = ZoneInfo("Europe/Moscow")
    series = _series(tz)
    instance_dt = series.start_dt
    moved_dt = instance_dt + timedelta(hours=2)
    updated = edit_instance_this(series, instance_dt, {"start_at": moved_dt, "title": "Йога+"})

    assert any(value.isoformat() == instance_dt.isoformat() for value in updated.exdates)
    assert moved_dt.isoformat() in updated.overrides
    assert updated.overrides[moved_dt.isoformat()]["title"] == "Йога+"


def test_edit_series_future_splits_exdates_and_overrides() -> None:
    tz = ZoneInfo("Europe/Moscow")
    series = _series(tz)
    split_at = series.start_dt + timedelta(days=3)
    series = replace(
        series,
        exdates=[series.start_dt + timedelta(days=1), series.start_dt + timedelta(days=4)],
        overrides={
            (series.start_dt + timedelta(days=1)).isoformat(): {"title": "До"},
            (series.start_dt + timedelta(days=4)).isoformat(): {"title": "После"},
        },
    )

    master, future = edit_series_future(series, split_at, {"title": "Новая серия"})

    assert master.exdates and future.exdates
    assert all(value < split_at for value in master.exdates)
    assert all(value >= split_at for value in future.exdates)
    assert list(master.overrides.keys()) == [(series.start_dt + timedelta(days=1)).isoformat()]
    assert list(future.overrides.keys()) == [(series.start_dt + timedelta(days=4)).isoformat()]
    assert future.title == "Новая серия"


def test_delete_series_future_trims_overrides() -> None:
    tz = ZoneInfo("Europe/Moscow")
    series = _series(tz)
    series = replace(
        series,
        overrides={
            (series.start_dt + timedelta(days=1)).isoformat(): {"title": "До"},
            (series.start_dt + timedelta(days=4)).isoformat(): {"title": "После"},
        },
    )
    split_at = series.start_dt + timedelta(days=2)
    updated = delete_series_future(series, split_at)

    assert list(updated.overrides.keys()) == [(series.start_dt + timedelta(days=1)).isoformat()]
    assert updated.rrule is not None and "UNTIL=" in updated.rrule


def test_delete_instance_this_dst_keeps_timezone() -> None:
    tz = ZoneInfo("Europe/Amsterdam")
    series = RecurrenceSeries(
        series_id="series-dst",
        start_dt=datetime(2026, 3, 29, 9, 0, tzinfo=tz),
        title="Звонок",
        rrule="FREQ=DAILY",
        exdates=[],
        overrides={},
        timezone=tz,
    )
    updated = delete_instance_this(series, series.start_dt)

    assert updated.exdates[0].tzinfo == tz
