from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
import re
import uuid

from app.core.recurrence_parse import RecurrenceParseResult, parse_recurrence


_DATE_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b")
_DATE_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_TIME_COLON_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_TIME_MARKER_RE = re.compile(
    r"\bв\s*(\d{1,2})(?::(\d{2}))?\s*(?:ч|час(?:а|ов)?)?\b",
    re.IGNORECASE,
)
_TIME_RANGE_RE = re.compile(
    r"(?:\bс\s*(\d{1,2})(?::(\d{2}))?\s*до\s*(\d{1,2})(?::(\d{2}))?\b|\b(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\b)",
    re.IGNORECASE,
)
_RELATIVE_RE = re.compile(
    r"через\s+(\d+)\s*(минут(?:у|ы)?|час(?:а|ов)?|дн(?:я|ей|ь))",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"на\s+(\d+)\s*(минут(?:у|ы)?|час(?:а|ов)?)",
    re.IGNORECASE,
)

_WEEKDAY_MAP = {
    "понедельник": 0,
    "пон": 0,
    "пн": 0,
    "вторник": 1,
    "вт": 1,
    "среда": 2,
    "среду": 2,
    "ср": 2,
    "четверг": 3,
    "чт": 3,
    "пятница": 4,
    "пятницу": 4,
    "пт": 4,
    "суббота": 5,
    "субботу": 5,
    "сб": 5,
    "воскресенье": 6,
    "вс": 6,
}

_DAY_PARTS = {
    "утром": 9,
    "днем": 13,
    "днём": 13,
    "вечером": 19,
    "ночью": 22,
}

_CALENDAR_INTENT_TOKENS = {
    "запиши",
    "добавь",
    "добавить",
    "встреча",
    "созвон",
    "прием",
    "приём",
    "урок",
    "занятие",
    "дедлайн",
    "напомни",
    "напоминание",
    "стендап",
}

_DATE_HINT_TOKENS = {
    "сегодня",
    "завтра",
    "послезавтра",
    "через",
    "каждый",
    "каждую",
    "каждое",
    "каждые",
    "будний",
    "будние",
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
}

_RECURRENCE_HINTS = ("кажд", "ежеднев", "будн", "кроме")


@dataclass
class EventDraft:
    title: str
    start_at: datetime | None
    end_at: datetime | None
    location: str | None
    recurrence: RecurrenceParseResult | None
    missing_fields: list[str]
    confidence: float | None = None
    duration_minutes: int | None = None
    date_hint: date | None = None
    time_hint: time | None = None
    end_time_hint: time | None = None
    source_text: str | None = None


@dataclass
class ParsedDateTime:
    date_value: date | None
    time_value: time | None
    end_time_value: time | None
    duration_minutes: int | None
    relative_dt: datetime | None
    time_explicit: bool
    date_explicit: bool
    date_spans: list[tuple[int, int]]


def generate_draft_id() -> str:
    return uuid.uuid4().hex[:8]


def is_calendar_intent(text: str) -> bool:
    lowered = text.lower()
    if not any(token in lowered for token in _CALENDAR_INTENT_TOKENS):
        return False
    return _has_date_or_time_hint(lowered)


def _has_date_or_time_hint(text: str) -> bool:
    if _DATE_RE.search(text) or _DATE_ISO_RE.search(text) or _TIME_COLON_RE.search(text):
        return True
    if _TIME_MARKER_RE.search(text):
        return True
    if _TIME_RANGE_RE.search(text):
        return True
    if _RELATIVE_RE.search(text):
        return True
    if _DURATION_RE.search(text):
        return True
    if any(token in text for token in _DATE_HINT_TOKENS):
        return True
    if any(part in text for part in _DAY_PARTS):
        return True
    return False


def event_from_text_ru(
    text: str,
    now: datetime,
    tz: tzinfo,
    last_state=None,
    default_duration_min: int = 60,
) -> EventDraft:
    parsed = _parse_datetime_components(text.lower(), now, tz)
    date_value = parsed.date_value
    time_value = parsed.time_value
    end_time_value = parsed.end_time_value
    duration_minutes = parsed.duration_minutes

    start_at = parsed.relative_dt
    end_at = None
    date_hint = date_value
    time_hint = time_value
    end_time_hint = end_time_value

    if start_at is None and date_value and time_value:
        start_at = datetime.combine(date_value, time_value).replace(tzinfo=tz)
    if start_at is None and date_value is None and time_value is not None and _has_recurrence_hint(text):
        date_value = now.astimezone(tz).date()
        date_hint = date_value
        start_at = datetime.combine(date_value, time_value).replace(tzinfo=tz)

    if start_at and end_time_value:
        end_at = datetime.combine(start_at.date(), end_time_value).replace(tzinfo=tz)
    if start_at and duration_minutes:
        end_at = start_at + timedelta(minutes=duration_minutes)

    recurrence = None
    if start_at is not None:
        recurrence = parse_recurrence(text, start_at, tz) if text else None

    title = _extract_title(text, parsed.date_spans)
    missing_fields: list[str] = []
    if not title:
        title = "Событие"
        missing_fields.append("title")

    if start_at is None:
        if date_value is None:
            missing_fields.append("date")
        if time_value is None and parsed.relative_dt is None and not parsed.time_explicit:
            missing_fields.append("time")
    else:
        if date_value is None and parsed.date_explicit is False:
            missing_fields.append("date")
        if not parsed.time_explicit:
            missing_fields.append("time")

    if start_at and end_at is None and parsed.time_explicit and default_duration_min > 0:
        end_at = start_at + timedelta(minutes=default_duration_min)

    return EventDraft(
        title=title,
        start_at=start_at,
        end_at=end_at,
        location=None,
        recurrence=recurrence,
        missing_fields=_dedupe_missing(missing_fields),
        duration_minutes=duration_minutes,
        date_hint=date_hint,
        time_hint=time_hint,
        end_time_hint=end_time_hint,
        source_text=text,
    )


def _has_recurrence_hint(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in _RECURRENCE_HINTS)


def update_draft_from_text(
    draft: EventDraft,
    text: str,
    now: datetime,
    tz: tzinfo,
) -> EventDraft:
    lowered = text.lower()
    parsed = _parse_datetime_components(lowered, now, tz)
    title = draft.title
    missing = set(draft.missing_fields)
    date_hint = draft.date_hint
    time_hint = draft.time_hint
    end_time_hint = draft.end_time_hint
    duration_minutes = draft.duration_minutes

    if "title" in missing:
        candidate = text.strip()
        if candidate:
            title = candidate
            missing.discard("title")

    if parsed.date_value:
        date_hint = parsed.date_value
    if parsed.time_value:
        time_hint = parsed.time_value
    if parsed.end_time_value:
        end_time_hint = parsed.end_time_value
    if parsed.duration_minutes is not None:
        duration_minutes = parsed.duration_minutes

    start_at = draft.start_at
    if parsed.relative_dt is not None:
        start_at = parsed.relative_dt
        missing.discard("date")
        missing.discard("time")
    elif date_hint and time_hint:
        start_at = datetime.combine(date_hint, time_hint).replace(tzinfo=tz)
        missing.discard("date")
        missing.discard("time")
    elif date_hint and "date" in missing:
        missing.discard("date")
    elif time_hint and "time" in missing:
        missing.discard("time")

    end_at = draft.end_at
    if start_at and end_time_hint:
        end_at = datetime.combine(start_at.date(), end_time_hint).replace(tzinfo=tz)
    elif start_at and duration_minutes:
        end_at = start_at + timedelta(minutes=duration_minutes)

    recurrence = draft.recurrence
    if start_at is not None and draft.source_text:
        recurrence = parse_recurrence(draft.source_text, start_at, tz)

    if start_at and end_at is None and parsed.time_explicit:
        end_at = start_at + timedelta(minutes=60)

    return EventDraft(
        title=title,
        start_at=start_at,
        end_at=end_at,
        location=draft.location,
        recurrence=recurrence,
        missing_fields=_order_missing(missing),
        duration_minutes=duration_minutes,
        date_hint=date_hint,
        time_hint=time_hint,
        end_time_hint=end_time_hint,
        source_text=draft.source_text,
    )


def parse_datetime_shift(
    text: str,
    *,
    base_dt: datetime,
    now: datetime,
    tz: tzinfo,
) -> datetime | None:
    lowered = text.lower()
    parsed = _parse_datetime_components(lowered, now, tz)
    if parsed.relative_dt is not None:
        return parsed.relative_dt
    if parsed.date_value or parsed.time_value:
        date_value = parsed.date_value or base_dt.date()
        time_value = parsed.time_value or base_dt.time()
        return datetime.combine(date_value, time_value).replace(tzinfo=tz)
    shift = _parse_shift_delta(lowered)
    if shift:
        return base_dt + shift
    return None


def _parse_shift_delta(text: str) -> timedelta | None:
    match = _DURATION_RE.search(text)
    if not match:
        return None
    value = _safe_int(match.group(1))
    if value is None:
        return None
    unit = match.group(2)
    if unit.startswith("мин"):
        return timedelta(minutes=value)
    if unit.startswith("час"):
        return timedelta(hours=value)
    return None


def _parse_datetime_components(text: str, now: datetime, tz: tzinfo) -> ParsedDateTime:
    date_spans: list[tuple[int, int]] = []
    date_value = None
    time_value = None
    end_time_value = None
    duration_minutes = None
    relative_dt = None
    time_explicit = False
    date_explicit = False

    date_value, date_spans, date_explicit = _parse_date(text, now, tz)

    relative_dt = _parse_relative_datetime(text, now, tz)
    if relative_dt is not None:
        date_explicit = True
        time_explicit = True
        return ParsedDateTime(
            date_value=relative_dt.date(),
            time_value=relative_dt.time(),
            end_time_value=None,
            duration_minutes=_parse_duration(text),
            relative_dt=relative_dt,
            time_explicit=True,
            date_explicit=True,
            date_spans=date_spans,
        )

    time_value, end_time_value, time_explicit = _parse_time(text, date_spans)
    if time_value is None:
        day_part = _parse_day_part(text)
        if day_part is not None:
            time_value = day_part
            time_explicit = True

    duration_minutes = _parse_duration(text)
    return ParsedDateTime(
        date_value=date_value,
        time_value=time_value,
        end_time_value=end_time_value,
        duration_minutes=duration_minutes,
        relative_dt=relative_dt,
        time_explicit=time_explicit,
        date_explicit=date_explicit,
        date_spans=date_spans,
    )


def _parse_date(text: str, now: datetime, tz: tzinfo) -> tuple[date | None, list[tuple[int, int]], bool]:
    spans: list[tuple[int, int]] = []
    match = _DATE_ISO_RE.search(text)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
        spans.append(match.span())
        return date(year, month, day), spans, True
    match = _DATE_RE.search(text)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year_raw = match.group(3)
        year = now.astimezone(tz).year
        if year_raw:
            year = int(year_raw)
            if year < 100:
                year += 2000
        spans.append(match.span())
        return date(year, month, day), spans, True

    lowered = text.lower()
    base_date = now.astimezone(tz).date()
    if "послезавтра" in lowered:
        return base_date + timedelta(days=2), spans, True
    if "завтра" in lowered:
        return base_date + timedelta(days=1), spans, True
    if "сегодня" in lowered:
        return base_date, spans, True

    weekday_date = _parse_weekday(text, now, tz)
    if weekday_date is not None:
        return weekday_date, spans, True

    match = _RELATIVE_RE.search(text)
    if match:
        value = _safe_int(match.group(1))
        unit = match.group(2)
        if value is not None and unit.startswith("дн"):
            return base_date + timedelta(days=value), spans, True

    return None, spans, False


def _parse_weekday(text: str, now: datetime, tz: tzinfo) -> date | None:
    lowered = text.lower()
    for token, weekday in _WEEKDAY_MAP.items():
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            local_now = now.astimezone(tz)
            target = _next_weekday(local_now.date(), local_now.weekday(), weekday)
            if "след" in lowered:
                target = target + timedelta(days=7)
            return target
    return None


def _next_weekday(current_date: date, current_weekday: int, target_weekday: int) -> date:
    delta = (target_weekday - current_weekday) % 7
    if delta == 0:
        delta = 7
    return current_date + timedelta(days=delta)


def _parse_relative_datetime(text: str, now: datetime, tz: tzinfo) -> datetime | None:
    match = _RELATIVE_RE.search(text)
    if not match:
        return None
    value = _safe_int(match.group(1))
    if value is None:
        return None
    unit = match.group(2)
    base = now.astimezone(tz)
    if unit.startswith("мин"):
        return base + timedelta(minutes=value)
    if unit.startswith("час"):
        return base + timedelta(hours=value)
    if unit.startswith("дн"):
        return base + timedelta(days=value)
    return None


def _parse_time(text: str, date_spans: list[tuple[int, int]]) -> tuple[time | None, time | None, bool]:
    cleaned = _mask_spans(text, date_spans)
    match = _TIME_RANGE_RE.search(cleaned)
    if match:
        if match.group(1):
            start_hour = int(match.group(1))
            start_minute = int(match.group(2) or 0)
            end_hour = int(match.group(3))
            end_minute = int(match.group(4) or 0)
        else:
            start_hour = int(match.group(5))
            start_minute = int(match.group(6))
            end_hour = int(match.group(7))
            end_minute = int(match.group(8))
        return time(start_hour, start_minute), time(end_hour, end_minute), True

    match = _TIME_COLON_RE.search(cleaned)
    if match:
        return time(int(match.group(1)), int(match.group(2))), None, True
    match = _TIME_MARKER_RE.search(cleaned)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        return time(hour, minute), None, True
    return None, None, False


def _parse_day_part(text: str) -> time | None:
    lowered = text.lower()
    for token, hour in _DAY_PARTS.items():
        if token in lowered:
            return time(hour, 0)
    return None


def _parse_duration(text: str) -> int | None:
    match = _DURATION_RE.search(text)
    if not match:
        return None
    value = _safe_int(match.group(1))
    if value is None:
        return None
    unit = match.group(2)
    if unit.startswith("мин"):
        return value
    if unit.startswith("час"):
        return value * 60
    return None


def _mask_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        for idx in range(start, end):
            chars[idx] = " "
    return "".join(chars)


def _extract_title(text: str, date_spans: list[tuple[int, int]]) -> str:
    cleaned = _mask_spans(text, date_spans)
    cleaned = _TIME_RANGE_RE.sub(" ", cleaned)
    cleaned = _TIME_COLON_RE.sub(" ", cleaned)
    cleaned = _TIME_MARKER_RE.sub(" ", cleaned)
    cleaned = _RELATIVE_RE.sub(" ", cleaned)
    cleaned = _DURATION_RE.sub(" ", cleaned)
    for token in _DATE_HINT_TOKENS:
        cleaned = re.sub(rf"\b{re.escape(token)}\b", " ", cleaned, flags=re.IGNORECASE)
    for token in _DAY_PARTS:
        cleaned = re.sub(rf"\b{re.escape(token)}\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dedupe_missing(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _order_missing(values: set[str]) -> list[str]:
    ordered = []
    for key in ("title", "date", "time"):
        if key in values:
            ordered.append(key)
    for value in sorted(values):
        if value not in ordered:
            ordered.append(value)
    return ordered
