from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core import calendar_store


@dataclass(frozen=True)
class RecurrenceSeries:
    series_id: str
    start_dt: datetime
    title: str
    rrule: str | None
    exdates: list[datetime]
    overrides: dict[str, dict[str, object]]
    timezone: ZoneInfo


def build_series(event: calendar_store.CalendarItem) -> RecurrenceSeries:
    tzinfo = _resolve_timezone(event)
    exdates = [value.astimezone(tzinfo) for value in (event.exdates or [])]
    overrides = event.overrides or {}
    series_id = event.series_id or event.id
    return RecurrenceSeries(
        series_id=series_id,
        start_dt=event.dt.astimezone(tzinfo),
        title=event.title,
        rrule=event.rrule,
        exdates=exdates,
        overrides=overrides,
        timezone=tzinfo,
    )


def edit_instance_this(series: RecurrenceSeries, instance_dt: datetime, patch: dict[str, object]) -> RecurrenceSeries:
    tzinfo = series.timezone
    instance_value = _ensure_tz(instance_dt, tzinfo)
    override_key = instance_value.isoformat()
    normalized_patch = _normalize_patch(patch, tzinfo)
    exdates = list(series.exdates)
    overrides = dict(series.overrides)
    new_start = normalized_patch.get("start_at")
    if isinstance(new_start, datetime) and new_start != instance_value:
        exdates.append(instance_value)
        override_key = new_start.isoformat()
    if normalized_patch:
        overrides[override_key] = normalized_patch
    return replace(series, exdates=_dedupe_exdates(exdates), overrides=overrides)


def edit_series_all(series: RecurrenceSeries, patch: dict[str, object]) -> RecurrenceSeries:
    tzinfo = series.timezone
    normalized_patch = _normalize_patch(patch, tzinfo)
    updated = series
    if "start_at" in normalized_patch and isinstance(normalized_patch["start_at"], datetime):
        updated = replace(updated, start_dt=normalized_patch["start_at"])
    if "title" in normalized_patch and isinstance(normalized_patch["title"], str):
        updated = replace(updated, title=normalized_patch["title"])
    if "rrule" in normalized_patch:
        rrule_value = normalized_patch["rrule"]
        if isinstance(rrule_value, str) or rrule_value is None:
            updated = replace(updated, rrule=rrule_value)
    return updated


def edit_series_future(
    series: RecurrenceSeries,
    instance_dt: datetime,
    patch: dict[str, object],
) -> tuple[RecurrenceSeries, RecurrenceSeries]:
    tzinfo = series.timezone
    instance_value = _ensure_tz(instance_dt, tzinfo)
    updated_rrule = _set_rrule_part(series.rrule, "UNTIL", _format_utc_value(instance_value - timedelta(seconds=1)))
    master_exdates, future_exdates = _split_exdates(series.exdates, instance_value)
    master_overrides, future_overrides = _split_overrides(series.overrides, instance_value, tzinfo)
    master_series = replace(
        series,
        rrule=updated_rrule,
        exdates=master_exdates,
        overrides=master_overrides,
    )
    normalized_patch = _normalize_patch(patch, tzinfo)
    future_start = normalized_patch.get("start_at") if isinstance(normalized_patch.get("start_at"), datetime) else instance_value
    future_title = normalized_patch.get("title") if isinstance(normalized_patch.get("title"), str) else series.title
    future_rrule = _strip_rrule_parts(series.rrule, {"UNTIL", "COUNT"})
    future_series = RecurrenceSeries(
        series_id=series.series_id,
        start_dt=future_start,
        title=future_title,
        rrule=future_rrule,
        exdates=future_exdates,
        overrides=future_overrides,
        timezone=tzinfo,
    )
    return master_series, future_series


def delete_instance_this(series: RecurrenceSeries, instance_dt: datetime) -> RecurrenceSeries:
    tzinfo = series.timezone
    instance_value = _ensure_tz(instance_dt, tzinfo)
    exdates = list(series.exdates)
    exdates.append(instance_value)
    overrides = dict(series.overrides)
    overrides.pop(instance_value.isoformat(), None)
    return replace(series, exdates=_dedupe_exdates(exdates), overrides=overrides)


def delete_series_future(series: RecurrenceSeries, instance_dt: datetime) -> RecurrenceSeries:
    tzinfo = series.timezone
    instance_value = _ensure_tz(instance_dt, tzinfo)
    updated_rrule = _set_rrule_part(series.rrule, "UNTIL", _format_utc_value(instance_value - timedelta(seconds=1)))
    master_exdates, _ = _split_exdates(series.exdates, instance_value)
    master_overrides, _ = _split_overrides(series.overrides, instance_value, tzinfo)
    return replace(series, rrule=updated_rrule, exdates=master_exdates, overrides=master_overrides)


def serialize_overrides(overrides: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for key, patch in overrides.items():
        if not isinstance(key, str) or not isinstance(patch, dict):
            continue
        payload: dict[str, object] = {}
        for field, patch_value in patch.items():
            if isinstance(patch_value, datetime):
                payload[field] = patch_value.astimezone(calendar_store.BOT_TZ).isoformat()
            else:
                payload[field] = patch_value
        if payload:
            result[key] = payload
    return result


def _normalize_patch(patch: dict[str, object], tzinfo: ZoneInfo) -> dict[str, object]:
    normalized: dict[str, object] = {}
    title = patch.get("title")
    if isinstance(title, str) and title.strip():
        normalized["title"] = title.strip()
    start_at = patch.get("start_at")
    if isinstance(start_at, datetime):
        normalized["start_at"] = _ensure_tz(start_at, tzinfo)
    rrule = patch.get("rrule")
    if isinstance(rrule, str) or rrule is None:
        if "rrule" in patch:
            normalized["rrule"] = rrule
    return normalized


def _ensure_tz(value: datetime, tzinfo: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=tzinfo)
    return value.astimezone(tzinfo)


def _split_exdates(exdates: list[datetime], pivot: datetime) -> tuple[list[datetime], list[datetime]]:
    before: list[datetime] = []
    after: list[datetime] = []
    for item in exdates:
        if item < pivot:
            before.append(item)
        else:
            after.append(item)
    return before, after


def _split_overrides(
    overrides: dict[str, dict[str, object]],
    pivot: datetime,
    tzinfo: ZoneInfo,
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    before: dict[str, dict[str, object]] = {}
    after: dict[str, dict[str, object]] = {}
    for key, patch in overrides.items():
        dt_value = _parse_override_key(key, tzinfo)
        if dt_value is None or dt_value < pivot:
            before[key] = patch
        else:
            after[key] = patch
    return before, after


def _parse_override_key(value: str, tzinfo: ZoneInfo) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tzinfo)
    return parsed.astimezone(tzinfo)


def _dedupe_exdates(exdates: list[datetime]) -> list[datetime]:
    seen: set[str] = set()
    result: list[datetime] = []
    for item in exdates:
        iso = item.astimezone(calendar_store.BOT_TZ).isoformat()
        if iso in seen:
            continue
        seen.add(iso)
        result.append(item)
    return result


def _parse_rrule(rrule: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for segment in rrule.split(";"):
        if "=" not in segment:
            continue
        key, value = segment.split("=", 1)
        if key and value:
            parts[key] = value
    return parts


def _format_rrule(parts: dict[str, str]) -> str:
    return ";".join(f"{key}={value}" for key, value in parts.items())


def _set_rrule_part(rrule: str | None, key: str, value: str) -> str | None:
    if not rrule:
        return None
    parts = _parse_rrule(rrule)
    parts[key] = value
    return _format_rrule(parts)


def _strip_rrule_parts(rrule: str | None, keys: set[str]) -> str | None:
    if not rrule:
        return rrule
    parts = _parse_rrule(rrule)
    for key in keys:
        parts.pop(key, None)
    if not parts:
        return None
    return _format_rrule(parts)


def _format_utc_value(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_timezone(event: calendar_store.CalendarItem) -> ZoneInfo:
    if event.timezone:
        try:
            return ZoneInfo(event.timezone)
        except Exception:
            return calendar_store.BOT_TZ
    if isinstance(event.dt.tzinfo, ZoneInfo):
        return event.dt.tzinfo
    return calendar_store.BOT_TZ
