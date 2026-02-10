"""Формирование текста «дайджеста дня»: события и напоминания на дату.

Фича опциональна, выключена по умолчанию. Если событий и напоминаний нет —
возвращается пустая строка (не слать сообщение, чтобы не спамить).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core import calendar_store


def build_daily_digest_text(
    events: list[calendar_store.CalendarItem],
    reminders: list[calendar_store.ReminderItem],
    *,
    for_date: date,
    tz: ZoneInfo,
    max_events: int = 20,
    max_reminders: int = 20,
) -> str:
    """Собирает текст дайджеста на дату: события и напоминания.

    Если и событий, и напоминаний нет — возвращает пустую строку (отправлять не нужно).
    """
    lines: list[str] = []
    day_start = datetime.combine(for_date, datetime.min.time()).replace(tzinfo=tz)
    day_end = day_start.replace(hour=23, minute=59, second=59, microsecond=999999)

    event_items = [e for e in events if e.dt >= day_start and e.dt <= day_end]
    event_items.sort(key=lambda e: e.dt)
    reminder_items = [
        r
        for r in reminders
        if r.enabled
        and r.status == "active"
        and day_start <= r.trigger_at <= day_end
    ]
    reminder_items.sort(key=lambda r: r.trigger_at)

    if not event_items and not reminder_items:
        return ""

    lines.append(f"📋 Дайджест на {for_date.strftime('%d.%m.%Y')}")
    if event_items:
        lines.append("\nСобытия:")
        for e in event_items[:max_events]:
            dt_label = e.dt.astimezone(tz).strftime("%H:%M")
            lines.append(f"  • {dt_label} — {e.title or '(без названия)'}")
    if reminder_items:
        lines.append("\nНапоминания:")
        for r in reminder_items[:max_reminders]:
            dt_label = r.trigger_at.astimezone(tz).strftime("%H:%M")
            lines.append(f"  • {dt_label} — {r.text or '(без текста)'}")

    return "\n".join(lines).strip()
