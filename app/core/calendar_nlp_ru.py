from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from app.core import calendar_store, recurrence_parse


@dataclass(frozen=True)
class RecurrenceSpec:
    rrule: str
    exdates: list[datetime]
    human: str
    raw_text: str


@dataclass
class EventDraft:
    title: str
    start_at: datetime | None
    end_at: datetime | None
    location: str | None
    recurrence: RecurrenceSpec | None
    missing_fields: list[str]


_STOPWORDS = {
    "запиши",
    "запишите",
    "добавь",
    "добавить",
    "встреча",
    "созвон",
    "созвонись",
    "созвониться",
    "прием",
    "приём",
    "урок",
    "занятие",
    "ивент",
    "дедлайн",
    "план",
    "событие",
    "напомни",
}
_DATE_RE = re.compile(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_RELATIVE_RE = re.compile(
    r"\b(сегодня|завтра|послезавтра|через\s+\d+\s+(?:минут|минуты|мин|м|час|часа|часов|ч|день|дня|дней))\b",
    re.IGNORECASE,
)
_WEEKDAY_RE = re.compile(
    r"\b(?:в\s+)?(понедельник|вторник|среду|среда|четверг|пятницу|пятница|субботу|суббота|воскресенье)\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"\b(?:в\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\b", re.IGNORECASE)
_TIME_RANGE_RE = re.compile(
    r"\b(?:с\s*)?(?P<start>\d{1,2}(?::\d{2})?)\s*(?:до|-)\s*(?P<end>\d{1,2}(?::\d{2})?)\b",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(r"\bна\s+(?P<value>\d+)\s*(?P<unit>минут|минуты|мин|м|час|часа|часов|ч)\b")
_PERIOD_HINTS = {"утром": 9, "днем": 13, "днём": 13, "вечером": 19}


def is_calendar_intent(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered:
        return False
    if not any(token in lowered for token in _STOPWORDS):
        return False
    return bool(
        _DATE_RE.search(lowered)
        or _ISO_DATE_RE.search(lowered)
        or _RELATIVE_RE.search(lowered)
        or _WEEKDAY_RE.search(lowered)
        or _TIME_RE.search(lowered)
    )


def parse_datetime_phrase_ru(text: str, *, now: datetime, tz: ZoneInfo) -> datetime | None:
    raw = (text or "").strip()
    if not raw:
        return None
    time_match = _TIME_RE.search(raw)
    period_hour = _match_period_hour(raw)
    date_phrase = _extract_date_phrase(raw)
    if time_match:
        time_value = _normalize_time(time_match.group("hour"), time_match.group("minute"))
    elif period_hour is not None:
        time_value = time(period_hour, 0)
    else:
        time_value = None
    if date_phrase:
        if time_value:
            candidate = f"{date_phrase} {time_value.strftime('%H:%M')}"
        else:
            candidate = date_phrase
        try:
            return calendar_store.parse_user_datetime(candidate, now=now).astimezone(tz)
        except ValueError:
            return None
    if time_value:
        base_date = now.astimezone(tz).date()
        return datetime.combine(base_date, time_value).replace(tzinfo=tz)
    try:
        return calendar_store.parse_user_datetime(raw, now=now).astimezone(tz)
    except ValueError:
        return None


def event_from_text_ru(
    text: str,
    *,
    now: datetime,
    tz: ZoneInfo,
    default_duration_min: int = 60,
) -> EventDraft:
    cleaned = (text or "").strip()
    lowered = cleaned.lower()
    missing_fields: list[str] = []
    time_range = _TIME_RANGE_RE.search(lowered)
    duration_minutes = _parse_duration(lowered)
    start_at = None
    end_at = None
    if time_range:
        date_phrase = _extract_date_phrase(lowered) or ""
        start_time = time_range.group("start")
        end_time = time_range.group("end")
        start_at = _parse_datetime_with_time(date_phrase, start_time, now=now, tz=tz)
        end_at = _parse_datetime_with_time(date_phrase, end_time, now=now, tz=tz)
        if start_at and end_at and end_at <= start_at:
            end_at = end_at + timedelta(days=1)
    if start_at is None:
        start_at = parse_datetime_phrase_ru(lowered, now=now, tz=tz)
    if start_at is None:
        missing_fields.append("start_at")
    if end_at is None and start_at is not None:
        if duration_minutes is not None:
            end_at = start_at + timedelta(minutes=duration_minutes)
        else:
            end_at = start_at + timedelta(minutes=default_duration_min)
    recurrence = None
    if start_at is None:
        recurrence_start = now.astimezone(tz)
    else:
        recurrence_start = start_at
    recurrence_parsed = recurrence_parse.parse_recurrence(lowered, recurrence_start, tz)
    if recurrence_parsed:
        recurrence = RecurrenceSpec(
            rrule=recurrence_parsed.rrule,
            exdates=recurrence_parsed.exdates,
            human=recurrence_parsed.human,
            raw_text=text,
        )
    title = _extract_title(cleaned)
    if not title:
        title = "Событие"
        missing_fields.append("title")
    return EventDraft(
        title=title,
        start_at=start_at,
        end_at=end_at,
        location=None,
        recurrence=recurrence,
        missing_fields=missing_fields,
    )


def _match_period_hour(text: str) -> int | None:
    lowered = text.lower()
    for key, hour in _PERIOD_HINTS.items():
        if key in lowered:
            return hour
    return None


def _extract_date_phrase(text: str) -> str | None:
    match = _ISO_DATE_RE.search(text) or _DATE_RE.search(text)
    if match:
        return match.group(0)
    match = _RELATIVE_RE.search(text)
    if match:
        return match.group(0)
    match = _WEEKDAY_RE.search(text)
    if match:
        return match.group(0)
    return None


def _normalize_time(hour_raw: str | None, minute_raw: str | None) -> time:
    hour = int(hour_raw or 0)
    minute = int(minute_raw or 0)
    hour = max(0, min(hour, 23))
    minute = max(0, min(minute, 59))
    return time(hour, minute)


def _parse_datetime_with_time(date_phrase: str, time_value: str, *, now: datetime, tz: ZoneInfo) -> datetime | None:
    if date_phrase:
        candidate = f"{date_phrase} {time_value}"
        try:
            return calendar_store.parse_user_datetime(candidate, now=now).astimezone(tz)
        except ValueError:
            return None
    base_date = now.astimezone(tz).date()
    try:
        parsed_time = calendar_store.parse_user_datetime(time_value, now=now).astimezone(tz)
        return parsed_time
    except ValueError:
        pass
    match = _TIME_RE.search(time_value)
    if not match:
        return None
    parsed_time = _normalize_time(match.group("hour"), match.group("minute"))
    return datetime.combine(base_date, parsed_time).replace(tzinfo=tz)


def _parse_duration(text: str) -> int | None:
    match = _DURATION_RE.search(text)
    if not match:
        return None
    value = int(match.group("value"))
    unit = match.group("unit")
    if unit.startswith("ч") or "час" in unit:
        return value * 60
    return value


def parse_duration_minutes(text: str) -> int | None:
    return _parse_duration(text)


def _extract_title(text: str) -> str:
    lowered = text.lower()
    cleaned = text
    for token in _STOPWORDS:
        cleaned = re.sub(rf"\\b{re.escape(token)}\\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = _TIME_RANGE_RE.sub("", cleaned)
    cleaned = _DURATION_RE.sub("", cleaned)
    cleaned = _TIME_RE.sub("", cleaned)
    cleaned = _DATE_RE.sub("", cleaned)
    cleaned = _ISO_DATE_RE.sub("", cleaned)
    cleaned = _RELATIVE_RE.sub("", cleaned)
    cleaned = _WEEKDAY_RE.sub("", cleaned)
    cleaned = cleaned.replace("  ", " ").strip(" ,.-")
    if cleaned:
        return cleaned.strip()
    return ""
