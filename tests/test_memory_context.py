from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app.core.memory_store import MemoryStore, build_llm_context
from app.infra.request_context import RequestContext


def _make_request_context() -> RequestContext:
    return RequestContext(
        correlation_id="corr-1",
        user_id=1,
        chat_id=2,
        message_id=3,
        timezone=None,
        ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
        env="dev",
    )


def test_build_llm_context_orders_and_logs(caplog) -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def now_provider() -> datetime:
        return now

    store = MemoryStore(max_items=5, ttl_seconds=3600, now_provider=now_provider)
    store.add(
        chat_id=2,
        user_id=1,
        role="user",
        kind="message",
        content="Первый вопрос",
        env="dev",
        correlation_id="corr-0",
    )
    store.add(
        chat_id=2,
        user_id=1,
        role="assistant",
        kind="result",
        content="Первый ответ",
        intent="intent.one",
        status="ok",
        env="dev",
        correlation_id="corr-0",
    )
    store.add(
        chat_id=2,
        user_id=1,
        role="user",
        kind="message",
        content="Второй вопрос",
        env="dev",
        correlation_id="corr-0",
    )
    request_context = _make_request_context()
    caplog.set_level(logging.INFO, logger="app.core.memory_store")

    context = build_llm_context(request_context, store, limit=10, max_chars=1000)

    assert context is not None
    assert context.index("Первый вопрос") < context.index("Второй вопрос")
    payload = json.loads(caplog.records[-1].message)
    assert payload["event"] == "memory.used"
    assert payload["items_used"] == 3


def test_build_llm_context_respects_length_limit() -> None:
    store = MemoryStore(max_items=5, ttl_seconds=3600)
    store.add(
        chat_id=2,
        user_id=1,
        role="user",
        kind="message",
        content="Очень длинное сообщение " * 20,
        env="prod",
        correlation_id="corr-0",
    )
    store.add(
        chat_id=2,
        user_id=1,
        role="assistant",
        kind="result",
        content="Ответ " * 20,
        intent="intent.long",
        status="ok",
        env="prod",
        correlation_id="corr-0",
    )
    request_context = _make_request_context()

    context = build_llm_context(request_context, store, limit=10, max_chars=120)

    assert context is not None
    assert len(context) <= 120
