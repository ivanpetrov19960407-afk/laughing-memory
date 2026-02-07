from __future__ import annotations

import asyncio
import json
from datetime import datetime

from app.core import calendar_store


def test_calendar_store_best_effort_migration(tmp_path, monkeypatch) -> None:
    path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(path))
    payload = {
        "events": [
            {
                "event_id": "evt-1",
                "dt_start": "2026-04-01T09:00:00+03:00",
                "text": "Старое событие",
                "created_at": "2026-03-20T10:00:00+03:00",
                "chat_id": 1,
                "user_id": 1,
                "rrule": "FREQ=DAILY",
                "exdates": ["2026-04-02T09:00:00+03:00"],
            }
        ],
        "reminders": [],
        "updated_at": datetime.now(tz=calendar_store.BOT_TZ).isoformat(),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    store = calendar_store.load_store()
    assert store.get("schema_version") == 1

    item = asyncio.run(calendar_store.get_event("evt-1"))
    assert item is not None
    assert item.series_id == "evt-1"
    assert item.timezone == calendar_store.BOT_TZ.key
