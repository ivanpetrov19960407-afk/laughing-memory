from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.memory_store import MemorySanitizer, MemoryStore


def test_memory_store_ring_buffer_limits_items() -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def now_provider() -> datetime:
        return now

    store = MemoryStore(max_items=3, ttl_seconds=3600, now_provider=now_provider)
    for index in range(4):
        store.add(
            chat_id=10,
            user_id=20,
            role="user",
            kind="message",
            content=f"msg-{index}",
            env="prod",
        )
    items = store.get_recent(chat_id=10, user_id=20, limit=10)
    assert [item.content for item in items] == ["msg-1", "msg-2", "msg-3"]


def test_memory_store_ttl_cleanup() -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def now_provider() -> datetime:
        return now

    store = MemoryStore(max_items=5, ttl_seconds=10, now_provider=now_provider)
    store.add(chat_id=1, user_id=2, role="user", kind="message", content="first", env="prod")
    now += timedelta(seconds=11)
    store.add(chat_id=1, user_id=2, role="user", kind="message", content="second", env="prod")
    items = store.get_recent(chat_id=1, user_id=2, limit=10)
    assert [item.content for item in items] == ["second"]


def test_memory_sanitizer_masks_sensitive_data() -> None:
    sanitizer = MemorySanitizer(prod_limit=500, dev_limit=2000)
    text = "Email test@example.com phone +1 555-123-4567 token ABCDEFGHIJKLMNOPQRSTUV"
    cleaned = sanitizer.sanitize(text, env="prod")
    assert "[email]" in cleaned
    assert "[phone]" in cleaned
    assert "[secret]" in cleaned
    assert "test@example.com" not in cleaned
    assert "ABCDEFGHIJKLMNOPQRSTUV" not in cleaned
