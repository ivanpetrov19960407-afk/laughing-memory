from __future__ import annotations

import asyncio
import calendar
import json
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BOT_TZ = ZoneInfo(os.getenv("BOT_TIMEZONE", "Europe/Vilnius"))
MOSCOW_TZ = BOT_TZ  # backward compatible alias
VIENNA_TZ = BOT_TZ  # backward compatible alias
@dataclass(frozen=True)
class CalendarItem:
    id: str
    ts: str
    title: str
    created_at: str
    dt: datetime
    chat_id: int
    user_id: int


@dataclass(frozen=True)
class ReminderItem:
    id: str
    event_id: str
    user_id: int
    chat_id: int
    trigger_at: datetime
    text: str
    enabled: bool
    sent_at: str | None
    status: str
    recurrence: dict[str, object] | None
    last_triggered_at: datetime | None


def _calendar_path() -> Path:
    return Path(os.getenv("CALENDAR_PATH", "data/calendar.json"))


def _default_store(now: datetime | None = None) -> dict[str, object]:
    timestamp = (now or datetime.now(tz=VIENNA_TZ)).isoformat()
    return {"events": [], "reminders": [], "updated_at": timestamp}


def load_store() -> dict[str, object]:
    path = _calendar_path()
    if not path.exists():
        return _default_store()
    try:
        with path.open("r", encoding="utf-8") as handle:
            store = json.load(handle)
            return _normalize_store(store)
    except json.JSONDecodeError:
        return _default_store()


_STORE_LOCK = asyncio.Lock()


def save_store_atomic(store: dict[str, object]) -> None:
    path = _calendar_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(store, handle, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def _normalize_store(store: dict[str, object]) -> dict[str, object]:
    if not isinstance(store, dict):
        return _default_store()
    if "events" in store and "reminders" in store:
        return store
    items = store.get("items") or []
    events: list[dict[str, object]] = []
    reminders: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        ts = item.get("ts")
        title = item.get("title")
        if not isinstance(item_id, str) or not isinstance(ts, str) or not isinstance(title, str):
            continue
        events.append(
            {
                "event_id": item_id,
                "dt_start": ts,
                "text": title,
                "created_at": item.get("created_at"),
                "chat_id": item.get("chat_id"),
                "user_id": item.get("user_id"),
            }
        )
        remind_at = item.get("remind_at") or ts
        reminders.append(
            {
                "reminder_id": item_id,
                "event_id": item_id,
                "user_id": item.get("user_id"),
                "chat_id": item.get("chat_id"),
                "trigger_at": remind_at,
                "text": title,
                "enabled": not bool(item.get("remind_sent", False)),
                "sent_at": item.get("sent_at"),
                "status": "active" if not bool(item.get("remind_sent", False)) else "done",
                "recurrence": None,
                "last_triggered_at": item.get("sent_at"),
            }
        )
    timestamp = store.get("updated_at") or datetime.now(tz=VIENNA_TZ).isoformat()
    return {"events": events, "reminders": reminders, "updated_at": timestamp}


def _parse_datetime(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=VIENNA_TZ)
    return parsed.astimezone(VIENNA_TZ)


def _normalize_status(item: dict[str, object]) -> str:
    status = item.get("status")
    if isinstance(status, str) and status in {"active", "disabled", "done"}:
        return status
    enabled = bool(item.get("enabled", True))
    return "active" if enabled else "disabled"


def _parse_recurrence(value: object) -> dict[str, object] | None:
    if isinstance(value, dict) and value.get("freq"):
        return dict(value)
    return None


def _parse_triggered_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=VIENNA_TZ)
    return parsed.astimezone(VIENNA_TZ)


def _build_reminder_item(
    *,
    reminder_id: str,
    event_id: str,
    user_id: int,
    chat_id: int,
    trigger_at: datetime,
    text: str,
    enabled: bool,
    sent_at: str | None,
    status: str,
    recurrence: dict[str, object] | None,
    last_triggered_at: datetime | None,
) -> ReminderItem:
    return ReminderItem(
        id=reminder_id,
        event_id=event_id,
        user_id=user_id,
        chat_id=chat_id,
        trigger_at=trigger_at,
        text=text,
        enabled=enabled,
        sent_at=sent_at,
        status=status,
        recurrence=recurrence,
        last_triggered_at=last_triggered_at,
    )


async def add_item(
    dt: datetime,
    title: str,
    chat_id: int,
    remind_at: datetime | None = None,
    user_id: int = 0,
    reminders_enabled: bool = True,
    event_id: str | None = None,
) -> dict[str, object]:
    async with _STORE_LOCK:
        store = load_store()
        events = list(store.get("events") or [])
        reminders = list(store.get("reminders") or [])
        existing_ids = {item.get("event_id") for item in events if isinstance(item, dict)}
        if isinstance(event_id, str) and event_id in existing_ids:
            existing_item = next(
                (item for item in events if isinstance(item, dict) and item.get("event_id") == event_id),
                None,
            )
            result: dict[str, object] = {"event": existing_item} if isinstance(existing_item, dict) else {"event": None}
            return result
        event_id = event_id if isinstance(event_id, str) else _generate_id(
            {item_id for item_id in existing_ids if isinstance(item_id, str)}
        )
        reminder_ids = {
            item.get("reminder_id") for item in reminders if isinstance(item, dict) and item.get("reminder_id")
        }
        reminder_id = _generate_id({item_id for item_id in reminder_ids if isinstance(item_id, str)})
        now_iso = datetime.now(tz=VIENNA_TZ).isoformat()
        remind_at_value = (remind_at or dt).astimezone(VIENNA_TZ).isoformat()
        event = {
            "event_id": event_id,
            "dt_start": dt.astimezone(VIENNA_TZ).isoformat(),
            "text": title,
            "created_at": now_iso,
            "chat_id": chat_id,
            "user_id": user_id,
        }
        reminder: dict[str, object] | None = None
        if remind_at is not None or reminders_enabled:
            reminder = {
                "reminder_id": reminder_id,
                "event_id": event_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "trigger_at": remind_at_value,
                "text": title,
                "enabled": reminders_enabled,
                "sent_at": None,
                "status": "active" if reminders_enabled else "disabled",
                "recurrence": None,
                "last_triggered_at": None,
            }
        events.append(event)
        if reminder is not None:
            reminders.append(reminder)
        store["events"] = events
        store["reminders"] = reminders
        store["updated_at"] = now_iso
        save_store_atomic(store)
        result: dict[str, object] = {"event": event}
        if reminder is not None:
            result["reminder"] = reminder
        return result


async def add_reminder(
    *,
    trigger_at: datetime,
    text: str,
    chat_id: int,
    user_id: int,
    recurrence: dict[str, object] | None = None,
    enabled: bool = True,
) -> ReminderItem:
    created = await add_item(
        dt=trigger_at,
        title=text,
        chat_id=chat_id,
        remind_at=trigger_at,
        user_id=user_id,
        reminders_enabled=enabled,
    )
    reminder_payload = created.get("reminder") if isinstance(created, dict) else None
    reminder_id = reminder_payload.get("reminder_id") if isinstance(reminder_payload, dict) else None
    if not isinstance(reminder_id, str):
        raise RuntimeError("Failed to create reminder")
    if recurrence:
        updated = await set_reminder_recurrence(reminder_id, recurrence)
        if updated is not None:
            return updated
    reminder = await get_reminder(reminder_id)
    if reminder is None:
        raise RuntimeError("Failed to load reminder")
    return reminder


async def list_items(start: datetime | None = None, end: datetime | None = None) -> list[CalendarItem]:
    async with _STORE_LOCK:
        store = load_store()
        items = store.get("events") or []
    result: list[CalendarItem] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ts = item.get("dt_start")
        title = item.get("text")
        item_id = item.get("event_id")
        created_at = item.get("created_at")
        if not isinstance(ts, str) or not isinstance(title, str) or not isinstance(item_id, str):
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=VIENNA_TZ)
        if start and dt < start:
            continue
        if end and dt > end:
            continue
        chat_id = item.get("chat_id")
        user_id = item.get("user_id")
        result.append(
            CalendarItem(
                id=item_id,
                ts=ts,
                title=title,
                created_at=str(created_at),
                dt=dt,
                chat_id=int(chat_id) if isinstance(chat_id, int) else 0,
                user_id=int(user_id) if isinstance(user_id, int) else 0,
            )
        )
    result.sort(key=lambda item: item.dt)
    return result


async def delete_item(item_id: str) -> tuple[bool, str | None]:
    async with _STORE_LOCK:
        store = load_store()
        events = list(store.get("events") or [])
        reminders = list(store.get("reminders") or [])
        kept_events = [
            item for item in events if isinstance(item, dict) and item.get("event_id") != item_id
        ]
        if len(kept_events) == len(events):
            return False, None
        removed_reminder_id = None
        kept_reminders = []
        for reminder in reminders:
            if not isinstance(reminder, dict):
                continue
            if reminder.get("event_id") == item_id:
                removed_reminder_id = reminder.get("reminder_id")
                continue
            kept_reminders.append(reminder)
        store["events"] = kept_events
        store["reminders"] = kept_reminders
        store["updated_at"] = datetime.now(tz=VIENNA_TZ).isoformat()
        save_store_atomic(store)
        return True, removed_reminder_id if isinstance(removed_reminder_id, str) else None


async def list_due_reminders(now: datetime, limit: int | None = None) -> list[ReminderItem]:
    async with _STORE_LOCK:
        store = load_store()
        items = store.get("reminders") or []
    result: list[ReminderItem] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        trigger_at = item.get("trigger_at")
        reminder_id = item.get("reminder_id")
        event_id = item.get("event_id")
        text = item.get("text")
        enabled = bool(item.get("enabled", True))
        if (
            not isinstance(trigger_at, str)
            or not isinstance(reminder_id, str)
            or not isinstance(event_id, str)
            or not isinstance(text, str)
        ):
            continue
        status = _normalize_status(item)
        remind_at = _parse_datetime(trigger_at, now)
        if not enabled or status != "active" or remind_at > now:
            continue
        recurrence = _parse_recurrence(item.get("recurrence"))
        last_triggered_at = _parse_triggered_at(item.get("last_triggered_at"))
        result.append(
            _build_reminder_item(
                reminder_id=reminder_id,
                event_id=event_id,
                user_id=int(item.get("user_id")) if isinstance(item.get("user_id"), int) else 0,
                chat_id=int(item.get("chat_id")) if isinstance(item.get("chat_id"), int) else 0,
                trigger_at=remind_at,
                text=text,
                enabled=enabled,
                sent_at=item.get("sent_at") if isinstance(item.get("sent_at"), str) else None,
                status=status,
                recurrence=recurrence,
                last_triggered_at=last_triggered_at,
            )
        )
    result.sort(key=lambda item: item.trigger_at)
    if limit is not None:
        return result[:limit]
    return result


async def mark_reminder_sent(reminder_id: str, sent_at: datetime, missed: bool = False) -> ReminderItem | None:
    async with _STORE_LOCK:
        store = load_store()
        reminders = list(store.get("reminders") or [])
        updated_item: dict[str, object] | None = None
        next_trigger: datetime | None = None
        for item in reminders:
            if isinstance(item, dict) and item.get("reminder_id") == reminder_id:
                trigger_value = item.get("trigger_at")
                if not isinstance(trigger_value, str):
                    break
                current_trigger = _parse_datetime(trigger_value, sent_at)
                recurrence = _parse_recurrence(item.get("recurrence"))
                if recurrence:
                    next_trigger = _next_recurrence_trigger(current_trigger, recurrence)
                if recurrence and next_trigger is not None:
                    item["trigger_at"] = next_trigger.astimezone(VIENNA_TZ).isoformat()
                    item["enabled"] = True
                    item["status"] = "active"
                else:
                    item["enabled"] = False
                    item["status"] = "done"
                if not missed:
                    item["sent_at"] = sent_at.astimezone(VIENNA_TZ).isoformat()
                elif "sent_at" not in item:
                    item["sent_at"] = sent_at.astimezone(VIENNA_TZ).isoformat()
                item["last_triggered_at"] = sent_at.astimezone(VIENNA_TZ).isoformat()
                updated_item = item
                break
        if updated_item is None:
            return None
        store["reminders"] = reminders
        store["updated_at"] = datetime.now(tz=VIENNA_TZ).isoformat()
        save_store_atomic(store)
    event_id = updated_item.get("event_id")
    text = updated_item.get("text")
    trigger_at_value = updated_item.get("trigger_at")
    if not isinstance(event_id, str) or not isinstance(text, str) or not isinstance(trigger_at_value, str):
        return None
    if updated_item.get("enabled") and updated_item.get("status") == "active" and next_trigger is not None:
        return _build_reminder_item(
            reminder_id=reminder_id,
            event_id=event_id,
            user_id=int(updated_item.get("user_id")) if isinstance(updated_item.get("user_id"), int) else 0,
            chat_id=int(updated_item.get("chat_id")) if isinstance(updated_item.get("chat_id"), int) else 0,
            trigger_at=_parse_datetime(trigger_at_value, sent_at),
            text=text,
            enabled=True,
            sent_at=updated_item.get("sent_at") if isinstance(updated_item.get("sent_at"), str) else None,
            status="active",
            recurrence=_parse_recurrence(updated_item.get("recurrence")),
            last_triggered_at=_parse_triggered_at(updated_item.get("last_triggered_at")),
        )
    return None


async def list_reminders(
    now: datetime,
    limit: int | None = 5,
    include_disabled: bool = False,
) -> list[ReminderItem]:
    async with _STORE_LOCK:
        store = load_store()
        reminders = list(store.get("reminders") or [])
    result: list[ReminderItem] = []
    for item in reminders:
        if not isinstance(item, dict):
            continue
        reminder_id = item.get("reminder_id")
        event_id = item.get("event_id")
        trigger_at = item.get("trigger_at")
        text = item.get("text")
        enabled = bool(item.get("enabled", True))
        if (
            not isinstance(reminder_id, str)
            or not isinstance(event_id, str)
            or not isinstance(trigger_at, str)
            or not isinstance(text, str)
        ):
            continue
        status = _normalize_status(item)
        if not include_disabled and (not enabled or status != "active"):
            continue
        trigger_dt = _parse_datetime(trigger_at, now)
        if trigger_dt < now:
            continue
        recurrence = _parse_recurrence(item.get("recurrence"))
        last_triggered_at = _parse_triggered_at(item.get("last_triggered_at"))
        result.append(
            _build_reminder_item(
                reminder_id=reminder_id,
                event_id=event_id,
                user_id=int(item.get("user_id")) if isinstance(item.get("user_id"), int) else 0,
                chat_id=int(item.get("chat_id")) if isinstance(item.get("chat_id"), int) else 0,
                trigger_at=trigger_dt,
                text=text,
                enabled=enabled,
                sent_at=item.get("sent_at") if isinstance(item.get("sent_at"), str) else None,
                status=status,
                recurrence=recurrence,
                last_triggered_at=last_triggered_at,
            )
        )
    result.sort(key=lambda item: item.trigger_at)
    if limit is None:
        return result
    return result[:limit]


async def get_reminder(reminder_id: str) -> ReminderItem | None:
    async with _STORE_LOCK:
        store = load_store()
        reminders = list(store.get("reminders") or [])
    for item in reminders:
        if not isinstance(item, dict) or item.get("reminder_id") != reminder_id:
            continue
        trigger_at = item.get("trigger_at")
        text = item.get("text")
        event_id = item.get("event_id")
        if not isinstance(trigger_at, str) or not isinstance(text, str) or not isinstance(event_id, str):
            return None
        trigger_dt = _parse_datetime(trigger_at, datetime.now(tz=VIENNA_TZ))
        status = _normalize_status(item)
        recurrence = _parse_recurrence(item.get("recurrence"))
        last_triggered_at = _parse_triggered_at(item.get("last_triggered_at"))
        return _build_reminder_item(
            reminder_id=reminder_id,
            event_id=event_id,
            user_id=int(item.get("user_id")) if isinstance(item.get("user_id"), int) else 0,
            chat_id=int(item.get("chat_id")) if isinstance(item.get("chat_id"), int) else 0,
            trigger_at=trigger_dt,
            text=text,
            enabled=bool(item.get("enabled", True)),
            sent_at=item.get("sent_at") if isinstance(item.get("sent_at"), str) else None,
            status=status,
            recurrence=recurrence,
            last_triggered_at=last_triggered_at,
        )
    return None


async def get_event(event_id: str) -> CalendarItem | None:
    async with _STORE_LOCK:
        store = load_store()
        events = list(store.get("events") or [])
    for item in events:
        if not isinstance(item, dict) or item.get("event_id") != event_id:
            continue
        ts = item.get("dt_start")
        title = item.get("text")
        created_at = item.get("created_at")
        if not isinstance(ts, str) or not isinstance(title, str):
            return None
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=VIENNA_TZ)
        chat_id = item.get("chat_id")
        user_id = item.get("user_id")
        return CalendarItem(
            id=event_id,
            ts=ts,
            title=title,
            created_at=str(created_at),
            dt=dt,
            chat_id=int(chat_id) if isinstance(chat_id, int) else 0,
            user_id=int(user_id) if isinstance(user_id, int) else 0,
        )
    return None


async def disable_reminder(reminder_id: str) -> bool:
    async with _STORE_LOCK:
        store = load_store()
        reminders = list(store.get("reminders") or [])
        updated = False
        for item in reminders:
            if isinstance(item, dict) and item.get("reminder_id") == reminder_id:
                item["enabled"] = False
                item["status"] = "disabled"
                updated = True
                break
        if not updated:
            return False
        store["reminders"] = reminders
        store["updated_at"] = datetime.now(tz=VIENNA_TZ).isoformat()
        save_store_atomic(store)
        return True


async def set_reminder_recurrence(reminder_id: str, recurrence: dict[str, object] | None) -> ReminderItem | None:
    async with _STORE_LOCK:
        store = load_store()
        reminders = list(store.get("reminders") or [])
        updated_item: dict[str, object] | None = None
        for item in reminders:
            if isinstance(item, dict) and item.get("reminder_id") == reminder_id:
                item["recurrence"] = recurrence
                updated_item = item
                break
        if updated_item is None:
            return None
        store["reminders"] = reminders
        store["updated_at"] = datetime.now(tz=VIENNA_TZ).isoformat()
        save_store_atomic(store)
    return await get_reminder(reminder_id)


async def enable_reminder(reminder_id: str) -> bool:
    async with _STORE_LOCK:
        store = load_store()
        reminders = list(store.get("reminders") or [])
        updated = False
        for item in reminders:
            if isinstance(item, dict) and item.get("reminder_id") == reminder_id:
                item["enabled"] = True
                item["status"] = "active"
                updated = True
                break
        if not updated:
            return False
        store["reminders"] = reminders
        store["updated_at"] = datetime.now(tz=VIENNA_TZ).isoformat()
        save_store_atomic(store)
        return True


async def apply_snooze(
    reminder_id: str,
    *,
    minutes: int,
    now: datetime | None = None,
    base_trigger_at: datetime | None = None,
) -> ReminderItem | None:
    offset = max(1, minutes)
    current_now = (now or datetime.now(tz=VIENNA_TZ)).astimezone(VIENNA_TZ)
    async with _STORE_LOCK:
        store = load_store()
        reminders = list(store.get("reminders") or [])
        updated_item: dict[str, object] | None = None
        new_trigger: datetime | None = None
        for item in reminders:
            if not isinstance(item, dict) or item.get("reminder_id") != reminder_id:
                continue
            status = _normalize_status(item)
            if status != "active":
                return None
            trigger_value = item.get("trigger_at")
            if not isinstance(trigger_value, str):
                return None
            current_trigger = _parse_datetime(trigger_value, current_now)
            base = max(current_now, base_trigger_at or current_trigger)
            new_trigger = base + timedelta(minutes=offset)
            item["trigger_at"] = new_trigger.astimezone(VIENNA_TZ).isoformat()
            item["enabled"] = True
            item["status"] = "active"
            updated_item = item
            break
        if updated_item is None or new_trigger is None:
            return None
        store["reminders"] = reminders
        store["updated_at"] = datetime.now(tz=VIENNA_TZ).isoformat()
        save_store_atomic(store)
    event_id = updated_item.get("event_id")
    text = updated_item.get("text")
    if not isinstance(event_id, str) or not isinstance(text, str):
        return None
    return _build_reminder_item(
        reminder_id=reminder_id,
        event_id=event_id,
        user_id=int(updated_item.get("user_id")) if isinstance(updated_item.get("user_id"), int) else 0,
        chat_id=int(updated_item.get("chat_id")) if isinstance(updated_item.get("chat_id"), int) else 0,
        trigger_at=new_trigger.astimezone(VIENNA_TZ),
        text=text,
        enabled=True,
        sent_at=updated_item.get("sent_at") if isinstance(updated_item.get("sent_at"), str) else None,
        status=_normalize_status(updated_item),
        recurrence=_parse_recurrence(updated_item.get("recurrence")),
        last_triggered_at=_parse_triggered_at(updated_item.get("last_triggered_at")),
    )


async def update_reminder_trigger(
    reminder_id: str,
    trigger_at: datetime,
    enabled: bool = True,
) -> ReminderItem | None:
    async with _STORE_LOCK:
        store = load_store()
        reminders = list(store.get("reminders") or [])
        updated_item: dict[str, object] | None = None
        for item in reminders:
            if isinstance(item, dict) and item.get("reminder_id") == reminder_id:
                item["trigger_at"] = trigger_at.astimezone(VIENNA_TZ).isoformat()
                item["enabled"] = enabled
                item["status"] = "active" if enabled else "disabled"
                updated_item = item
                break
        if updated_item is None:
            return None
        store["reminders"] = reminders
        store["updated_at"] = datetime.now(tz=VIENNA_TZ).isoformat()
        save_store_atomic(store)
    event_id = updated_item.get("event_id")
    text = updated_item.get("text")
    if not isinstance(event_id, str) or not isinstance(text, str):
        return None
    return _build_reminder_item(
        reminder_id=reminder_id,
        event_id=event_id,
        user_id=int(updated_item.get("user_id")) if isinstance(updated_item.get("user_id"), int) else 0,
        chat_id=int(updated_item.get("chat_id")) if isinstance(updated_item.get("chat_id"), int) else 0,
        trigger_at=trigger_at.astimezone(VIENNA_TZ),
        text=text,
        enabled=enabled,
        sent_at=updated_item.get("sent_at") if isinstance(updated_item.get("sent_at"), str) else None,
        status=_normalize_status(updated_item),
        recurrence=_parse_recurrence(updated_item.get("recurrence")),
        last_triggered_at=_parse_triggered_at(updated_item.get("last_triggered_at")),
    )


async def delete_reminder(reminder_id: str) -> bool:
    async with _STORE_LOCK:
        store = load_store()
        reminders = list(store.get("reminders") or [])
        kept = [item for item in reminders if not (isinstance(item, dict) and item.get("reminder_id") == reminder_id)]
        if len(kept) == len(reminders):
            return False
        store["reminders"] = kept
        store["updated_at"] = datetime.now(tz=VIENNA_TZ).isoformat()
        save_store_atomic(store)
        return True


async def ensure_reminder_for_event(
    event: CalendarItem,
    trigger_at: datetime,
    enabled: bool = True,
) -> ReminderItem:
    async with _STORE_LOCK:
        store = load_store()
        reminders = list(store.get("reminders") or [])
        for item in reminders:
            if isinstance(item, dict) and item.get("event_id") == event.id:
                item["enabled"] = enabled
                item["trigger_at"] = trigger_at.astimezone(VIENNA_TZ).isoformat()
                item["status"] = "active" if enabled else "disabled"
                store["reminders"] = reminders
                store["updated_at"] = datetime.now(tz=VIENNA_TZ).isoformat()
                save_store_atomic(store)
                return _build_reminder_item(
                    reminder_id=str(item.get("reminder_id")),
                    event_id=event.id,
                    user_id=event.user_id,
                    chat_id=event.chat_id,
                    trigger_at=trigger_at.astimezone(VIENNA_TZ),
                    text=event.title,
                    enabled=enabled,
                    sent_at=item.get("sent_at") if isinstance(item.get("sent_at"), str) else None,
                    status=_normalize_status(item),
                    recurrence=_parse_recurrence(item.get("recurrence")),
                    last_triggered_at=_parse_triggered_at(item.get("last_triggered_at")),
                )
        reminder_id = _generate_id(
            {
                item_id
                for item_id in (item.get("reminder_id") for item in reminders if isinstance(item, dict))
                if isinstance(item_id, str)
            }
        )
        reminder = {
            "reminder_id": reminder_id,
            "event_id": event.id,
            "user_id": event.user_id,
            "chat_id": event.chat_id,
            "trigger_at": trigger_at.astimezone(VIENNA_TZ).isoformat(),
            "text": event.title,
            "enabled": enabled,
            "sent_at": None,
            "status": "active" if enabled else "disabled",
            "recurrence": None,
            "last_triggered_at": None,
        }
        reminders.append(reminder)
        store["reminders"] = reminders
        store["updated_at"] = datetime.now(tz=VIENNA_TZ).isoformat()
        save_store_atomic(store)
        return _build_reminder_item(
            reminder_id=reminder_id,
            event_id=event.id,
            user_id=event.user_id,
            chat_id=event.chat_id,
            trigger_at=trigger_at.astimezone(VIENNA_TZ),
            text=event.title,
            enabled=enabled,
            sent_at=None,
            status="active" if enabled else "disabled",
            recurrence=None,
            last_triggered_at=None,
        )


def parse_local_datetime(value: str) -> datetime:
    raw = value.strip()
    formats = ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%Y.%m.%d %H:%M", "%d-%m-%Y %H:%M")
    for fmt in formats:
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.replace(tzinfo=VIENNA_TZ)
        except ValueError:
            continue
    raise ValueError("Формат: YYYY-MM-DD HH:MM или DD.MM.YYYY HH:MM")


def parse_user_datetime(value: str, *, now: datetime | None = None) -> datetime:
    raw = value.strip()
    lowered = raw.lower()
    if lowered.startswith(("сегодня", "today", "завтра", "tomorrow")):
        parts = raw.split(maxsplit=1)
        if len(parts) < 2:
            raise ValueError("Добавь время, например: сегодня 18:30")
        time_part = parts[1].strip()
        try:
            parsed_time = datetime.strptime(time_part, "%H:%M").time()
        except ValueError as exc:
            raise ValueError("Формат времени: HH:MM") from exc
        base = (now or datetime.now(tz=VIENNA_TZ)).date()
        if lowered.startswith(("завтра", "tomorrow")):
            base = base + timedelta(days=1)
        return _combine_local(base, parsed_time)
    return parse_local_datetime(raw)


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Формат даты: YYYY-MM-DD") from exc


def day_bounds(target: date) -> tuple[datetime, datetime]:
    start = datetime.combine(target, time.min).replace(tzinfo=VIENNA_TZ)
    end = datetime.combine(target, time.max).replace(tzinfo=VIENNA_TZ)
    return start, end


def week_bounds(today: date) -> tuple[datetime, datetime]:
    start = datetime.combine(today, time.min).replace(tzinfo=VIENNA_TZ)
    end = (start + timedelta(days=7)) - timedelta(seconds=1)
    return start, end


def _combine_local(target_date: date, target_time: time) -> datetime:
    return datetime.combine(target_date, target_time).replace(tzinfo=VIENNA_TZ)


def _next_recurrence_trigger(trigger_at: datetime, recurrence: dict[str, object]) -> datetime | None:
    freq = recurrence.get("freq")
    local_trigger = trigger_at.astimezone(VIENNA_TZ)
    target_time = local_trigger.time()
    if freq == "daily":
        return _combine_local(local_trigger.date() + timedelta(days=1), target_time)
    if freq == "weekdays":
        weekdays = {0, 1, 2, 3, 4}
    elif freq == "weekly":
        byweekday = recurrence.get("byweekday")
        if isinstance(byweekday, list):
            weekdays = {day for day in byweekday if isinstance(day, int)}
        else:
            weekdays = set()
        if not weekdays:
            weekdays = {local_trigger.weekday()}
    else:
        weekdays = set()
    if freq in {"weekly", "weekdays"}:
        current_weekday = local_trigger.weekday()
        candidates = []
        for day in weekdays:
            if 0 <= day <= 6:
                delta = (day - current_weekday) % 7
                if delta == 0:
                    delta = 7
                candidates.append(delta)
        if not candidates:
            return None
        next_date = local_trigger.date() + timedelta(days=min(candidates))
        return _combine_local(next_date, target_time)
    if freq == "monthly":
        bymonthday = recurrence.get("bymonthday")
        if isinstance(bymonthday, int) and bymonthday > 0:
            target_day = bymonthday
        else:
            target_day = local_trigger.day
        year = local_trigger.year
        month = local_trigger.month + 1
        if month > 12:
            month = 1
            year += 1
        last_day = calendar.monthrange(year, month)[1]
        target_day = min(target_day, last_day)
        return _combine_local(date(year, month, target_day), target_time)
    return None


def _generate_id(existing_ids: set[str]) -> str:
    while True:
        candidate = uuid.uuid4().hex[:8]
        if candidate not in existing_ids:
            return candidate


def _selftest() -> None:
    dt = parse_local_datetime("2026-02-05 18:30")
    assert dt.tzinfo == VIENNA_TZ
    assert dt.year == 2026 and dt.month == 2 and dt.day == 5 and dt.hour == 18
    parsed_date = parse_date("2026-02-05")
    start, end = day_bounds(parsed_date)
    assert start.tzinfo == VIENNA_TZ
    assert end.tzinfo == VIENNA_TZ


if __name__ == "__main__":
    _selftest()
    print("calendar selftest ok")
