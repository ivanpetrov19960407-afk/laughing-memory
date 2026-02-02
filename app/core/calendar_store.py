from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

VIENNA_TZ = ZoneInfo("Europe/Vienna")


@dataclass(frozen=True)
class CalendarItem:
    id: str
    ts: str
    title: str
    created_at: str
    dt: datetime


def _calendar_path() -> Path:
    return Path(os.getenv("CALENDAR_PATH", "data/calendar.json"))


def _default_store(now: datetime | None = None) -> dict[str, object]:
    timestamp = (now or datetime.now(tz=VIENNA_TZ)).isoformat()
    return {"items": [], "updated_at": timestamp}


def load_store() -> dict[str, object]:
    path = _calendar_path()
    if not path.exists():
        return _default_store()
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError:
        return _default_store()


def save_store_atomic(store: dict[str, object]) -> None:
    path = _calendar_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(store, handle, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def add_item(dt: datetime, title: str) -> dict[str, str]:
    store = load_store()
    items = list(store.get("items") or [])
    existing_ids = {item.get("id") for item in items if isinstance(item, dict)}
    item_id = _generate_id(existing_ids)
    now_iso = datetime.now(tz=VIENNA_TZ).isoformat()
    item = {
        "id": item_id,
        "ts": dt.astimezone(VIENNA_TZ).isoformat(),
        "title": title,
        "created_at": now_iso,
    }
    items.append(item)
    store["items"] = items
    store["updated_at"] = now_iso
    save_store_atomic(store)
    return item


def list_items(start: datetime | None = None, end: datetime | None = None) -> list[CalendarItem]:
    store = load_store()
    items = store.get("items") or []
    result: list[CalendarItem] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ts = item.get("ts")
        title = item.get("title")
        item_id = item.get("id")
        created_at = item.get("created_at")
        if not isinstance(ts, str) or not isinstance(title, str) or not isinstance(item_id, str):
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if start and dt < start:
            continue
        if end and dt > end:
            continue
        result.append(
            CalendarItem(
                id=item_id,
                ts=ts,
                title=title,
                created_at=str(created_at),
                dt=dt,
            )
        )
    result.sort(key=lambda item: item.dt)
    return result


def delete_item(item_id: str) -> bool:
    store = load_store()
    items = list(store.get("items") or [])
    kept = [item for item in items if isinstance(item, dict) and item.get("id") != item_id]
    if len(kept) == len(items):
        return False
    store["items"] = kept
    store["updated_at"] = datetime.now(tz=VIENNA_TZ).isoformat()
    save_store_atomic(store)
    return True


def parse_local_datetime(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise ValueError("Формат: YYYY-MM-DD HH:MM") from exc
    return parsed.replace(tzinfo=VIENNA_TZ)


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
