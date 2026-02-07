from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.infra.trace_store import TraceEntry, TraceStore


def _entry(correlation_id: str, ts: datetime) -> TraceEntry:
    return TraceEntry(
        correlation_id=correlation_id,
        ts=ts,
        intent="test",
        mode="local",
        status="ok",
        total_duration_ms=12.5,
        trace_steps=[],
        durations={"total_ms": 12.5},
        tool_names=[],
        llm_models=[],
        tool_calls=[],
        llm_calls=[],
        error=None,
    )


def test_trace_store_ring_buffer_and_ttl() -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    clock = {"value": now}
    store = TraceStore(max_items=2, ttl_seconds=10, now_provider=lambda: clock["value"])

    store.add_entry(chat_id=1, user_id=2, entry=_entry("a", now))
    store.add_entry(chat_id=1, user_id=2, entry=_entry("b", now + timedelta(seconds=1)))
    store.add_entry(chat_id=1, user_id=2, entry=_entry("c", now + timedelta(seconds=2)))

    entries = store.list_entries(chat_id=1, user_id=2, limit=5)
    assert [entry.correlation_id for entry in entries] == ["c", "b"]

    clock["value"] = now + timedelta(seconds=20)
    assert store.list_entries(chat_id=1, user_id=2, limit=5) == []
