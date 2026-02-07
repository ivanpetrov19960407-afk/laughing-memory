from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
import re
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class RecurrenceParseResult:
    rrule: str
    exdates: list[datetime]
    human: str


_WEEKDAY_ALIASES = {
    "понедельник": "MO",
    "понедельникам": "MO",
    "пон": "MO",
    "пн": "MO",
    "вторник": "TU",
    "вторникам": "TU",
    "вт": "TU",
    "среда": "WE",
    "среду": "WE",
    "средам": "WE",
    "ср": "WE",
    "четверг": "TH",
    "четвергам": "TH",
    "чт": "TH",
    "пятница": "FR",
    "пятницу": "FR",
    "пятницам": "FR",
    "пт": "FR",
    "суббота": "SA",
    "субботу": "SA",
    "субботам": "SA",
    "сб": "SA",
    "воскресенье": "SU",
    "воскресеньям": "SU",
    "вс": "SU",
}
_WEEKDAY_ORDER = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_WEEKDAY_HUMAN = {
    "MO": "пн",
    "TU": "вт",
    "WE": "ср",
    "TH": "чт",
    "FR": "пт",
    "SA": "сб",
    "SU": "вс",
}

_DATE_RE = re.compile(r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?")


def parse_recurrence(text: str, start_dt: datetime, tz: ZoneInfo) -> RecurrenceParseResult | None:
    if not text:
        return None
    lowered = text.lower()

    exdates = _extract_exdates(lowered, start_dt, tz)
    count = _extract_count(lowered)
    until = _extract_until(lowered, start_dt, tz)
    interval = _extract_interval(lowered)

    freq = None
    byday: list[str] = []
    bymonthday: int | None = None

    if _contains_any(lowered, ["ежедневно", "каждый день"]):
        freq = "DAILY"
    elif _contains_any(lowered, ["будний", "по будням"]):
        freq = "WEEKLY"
        byday = ["MO", "TU", "WE", "TH", "FR"]
    else:
        byday = _extract_weekdays(lowered)
        if byday:
            freq = "WEEKLY"

    if freq is None:
        bymonthday = _extract_monthday(lowered)
        if bymonthday is not None:
            freq = "MONTHLY"

    if freq is None:
        return None

    if freq == "WEEKLY" and not byday:
        byday = [_weekday_code(start_dt.astimezone(tz).weekday())]

    components = [f"FREQ={freq}"]
    if freq == "WEEKLY" and byday:
        components.append(f"BYDAY={','.join(byday)}")
    if freq == "MONTHLY" and bymonthday is not None:
        components.append(f"BYMONTHDAY={bymonthday}")
    if interval and interval > 1:
        components.append(f"INTERVAL={interval}")
    if count:
        components.append(f"COUNT={count}")
    if until:
        components.append(f"UNTIL={_format_utc(until)}")

    rrule = ";".join(components)
    human = _build_human(freq, byday, bymonthday, interval, count, until, start_dt, tz)
    return RecurrenceParseResult(rrule=rrule, exdates=exdates, human=human)


def _contains_any(text: str, parts: list[str]) -> bool:
    return any(part in text for part in parts)


def _extract_weekdays(text: str) -> list[str]:
    found: set[str] = set()
    for token, code in _WEEKDAY_ALIASES.items():
        if re.search(rf"\b{re.escape(token)}\b", text):
            found.add(code)
    return [code for code in _WEEKDAY_ORDER if code in found]


def _extract_monthday(text: str) -> int | None:
    match = re.search(r"кажд(?:ый|ую|ое)?\s+месяц\s+(\d{1,2})", text)
    if not match:
        return None
    day = int(match.group(1))
    if 1 <= day <= 31:
        return day
    return None


def _extract_interval(text: str) -> int | None:
    match = re.search(r"кажд(?:ые|ую|ая|ый)?\s+(\d+)\s*-?\s*(?:ю|ую|ой)?\s+недел", text)
    if not match:
        match = re.search(r"каждые\s+(\d+)\s+недел", text)
    if not match:
        return None
    try:
        value = int(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def _extract_count(text: str) -> int | None:
    match = re.search(r"(\d+)\s+раз", text)
    if not match:
        return None
    try:
        value = int(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def _extract_until(text: str, start_dt: datetime, tz: ZoneInfo) -> datetime | None:
    match = re.search(r"до\s+(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)", text)
    if not match:
        return None
    parsed = _parse_date(match.group(1), start_dt)
    target_time = start_dt.astimezone(tz).time()
    return datetime.combine(parsed, target_time).replace(tzinfo=tz)


def _extract_exdates(text: str, start_dt: datetime, tz: ZoneInfo) -> list[datetime]:
    match = re.search(r"кроме\s+(.+)$", text)
    if not match:
        return []
    candidates = re.split(r"[,;]\s*", match.group(1))
    result: list[datetime] = []
    for raw in candidates:
        raw = raw.strip()
        if not raw:
            continue
        date_match = _DATE_RE.search(raw)
        if not date_match:
            continue
        parsed_date = _parse_date(date_match.group(0), start_dt)
        result.append(datetime.combine(parsed_date, start_dt.astimezone(tz).time()).replace(tzinfo=tz))
    return result


def _parse_date(value: str, start_dt: datetime) -> date:
    match = _DATE_RE.search(value)
    if not match:
        raise ValueError("invalid_date")
    day = int(match.group(1))
    month = int(match.group(2))
    year_raw = match.group(3)
    if year_raw:
        year = int(year_raw)
        if year < 100:
            year += 2000
    else:
        year = start_dt.year
    return date(year, month, day)


def _format_utc(value: datetime) -> str:
    return value.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")


def _weekday_code(index: int) -> str:
    return _WEEKDAY_ORDER[index % 7]


def _build_human(
    freq: str,
    byday: list[str],
    bymonthday: int | None,
    interval: int | None,
    count: int | None,
    until: datetime | None,
    start_dt: datetime,
    tz: ZoneInfo,
) -> str:
    time_label = start_dt.astimezone(tz).strftime("%H:%M")
    if freq == "DAILY":
        base = "каждый день"
    elif freq == "MONTHLY" and bymonthday is not None:
        base = f"каждый месяц {bymonthday}-го"
    elif freq == "WEEKLY" and byday:
        day_labels = ", ".join(_WEEKDAY_HUMAN.get(code, code.lower()) for code in byday)
        if interval and interval > 1:
            base = f"каждые {interval} недели по {day_labels}"
        else:
            base = f"по {day_labels}"
    else:
        base = "повтор"
    suffix = f" в {time_label}"
    if until:
        suffix += f", до {until.astimezone(tz).strftime('%d.%m.%Y')}"
    if count:
        suffix += f", {count} раз"
    return f"{base}{suffix}"
