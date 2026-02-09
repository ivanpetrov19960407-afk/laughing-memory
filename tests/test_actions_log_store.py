from __future__ import annotations

from datetime import datetime, timezone

from app.infra.actions_log_store import ActionsLogStore


def test_actions_log_store_append_and_search(tmp_path) -> None:
    store = ActionsLogStore(tmp_path / "actions.db")
    timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
    store.append(
        user_id=1,
        action_type="calendar.event.create",
        payload={"summary": "Создал встречу"},
        ts=timestamp,
        correlation_id="req-1",
    )

    entries = store.search(user_id=1, query=None, limit=10)
    assert len(entries) == 1
    assert entries[0].action_type == "calendar.event.create"

    filtered = store.search(user_id=1, query="type:calendar", limit=10)
    assert len(filtered) == 1

    empty = store.search(user_id=1, query="reminder", limit=10)
    assert empty == []

    recent = store.list_recent(user_id=1, limit=5)
    assert len(recent) == 1

    # since filter: only entries with ts >= since
    since_before = datetime(2023, 12, 1, tzinfo=timezone.utc)
    since_after = datetime(2024, 6, 1, tzinfo=timezone.utc)
    assert len(store.search(user_id=1, since=since_before, limit=10)) == 1
    assert len(store.search(user_id=1, since=since_after, limit=10)) == 0
