from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core import calendar_store

LOGGER = logging.getLogger(__name__)

DIGEST_TZ = ZoneInfo("Europe/Vilnius")


@dataclass(frozen=True)
class DigestData:
    date_key: str
    events: list[calendar_store.CalendarItem]
    reminders: list[calendar_store.ReminderItem]


def _date_key(now: datetime, tz: ZoneInfo) -> str:
    local = now.astimezone(tz)
    return local.date().isoformat()


def _day_bounds(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    local = now.astimezone(tz)
    start = datetime.combine(local.date(), time.min).replace(tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


async def collect_daily_digest(
    *,
    user_id: int,
    now: datetime | None = None,
    tz: ZoneInfo = DIGEST_TZ,
    max_events: int = 8,
    max_reminders: int = 8,
) -> DigestData:
    current = now or datetime.now(tz=tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    start, end = _day_bounds(current, tz)
    date_key = _date_key(current, tz)

    events_all = await calendar_store.list_items(start=start, end=end)
    events = [item for item in events_all if item.user_id == user_id]
    events.sort(key=lambda item: item.dt)
    if max_events:
        events = events[: max_events]

    reminders_all = await calendar_store.list_reminders(current, limit=None, include_disabled=False)
    reminders = [
        item
        for item in reminders_all
        if item.user_id == user_id and start <= item.trigger_at.astimezone(tz) < end
    ]
    reminders.sort(key=lambda item: item.trigger_at)
    if max_reminders:
        reminders = reminders[: max_reminders]

    return DigestData(date_key=date_key, events=events, reminders=reminders)


def render_daily_digest(data: DigestData, *, tz: ZoneInfo = DIGEST_TZ) -> str | None:
    if not data.events and not data.reminders:
        return None
    lines: list[str] = ["🗞 Утренний дайджест", f"Сегодня ({data.date_key}):"]
    if data.events:
        lines.append("🗓 События:")
        for item in data.events:
            when = item.dt.astimezone(tz).strftime("%H:%M")
            lines.append(f"- {when} — {item.title}")
    if data.reminders:
        lines.append("⏰ Напоминания:")
        for item in data.reminders:
            when = item.trigger_at.astimezone(tz).strftime("%H:%M")
            lines.append(f"- {when} — {item.text}")
    return "\n".join(lines).strip()

