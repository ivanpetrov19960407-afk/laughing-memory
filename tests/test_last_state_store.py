from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.infra.last_state_store import LastStateStore


def test_last_state_store_ttl_eviction() -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    clock = {"value": now}
    store = LastStateStore(ttl_seconds=10, now_provider=lambda: clock["value"])

    store.update_state(chat_id=1, user_id=2, intent="test.example", correlation_id="cid-1", event_id="evt-1")
    assert store.get_state(chat_id=1, user_id=2) is not None

    clock["value"] = now + timedelta(seconds=11)
    assert store.get_state(chat_id=1, user_id=2) is None
