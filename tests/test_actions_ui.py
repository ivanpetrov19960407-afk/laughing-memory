from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.bot import handlers
from app.infra.rate_limiter import RateLimiter


class DummyContext:
    def __init__(self) -> None:
        self.application = SimpleNamespace(
            bot_data={
                "action_store": handlers.ActionPayloadStore(),
                "ui_rate_limiter": RateLimiter(),
                "rate_limiter": RateLimiter(),
            }
        )
        self.chat_data: dict[str, object] = {}


class DummyUpdate:
    def __init__(self, user_id: int = 1, chat_id: int = 10) -> None:
        self.effective_user = SimpleNamespace(id=user_id, username="tester")
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.message = SimpleNamespace(text="/menu")
        self.effective_message = self.message


def test_action_payload_store_ttl_and_security(monkeypatch) -> None:
    clock = {"value": 100.0}

    def fake_monotonic() -> float:
        return clock["value"]

    monkeypatch.setattr(handlers.time, "monotonic", fake_monotonic)
    store = handlers.ActionPayloadStore(ttl_seconds=5, max_items=10)
    token = store.store(user_id=1, chat_id=2, action_id="test", payload={"op": "menu_open"})

    assert store.pop(user_id=1, chat_id=999, token=token) is None
    stored = store.pop(user_id=1, chat_id=2, token=token)
    assert stored is not None
    assert stored.payload["op"] == "menu_open"

    token_expired = store.store(user_id=1, chat_id=2, action_id="test", payload={"op": "menu_open"})
    clock["value"] += 10
    assert store.pop(user_id=1, chat_id=2, token=token_expired) is None


def test_menu_command_returns_actions(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        return True

    monkeypatch.setattr(handlers, "_send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    update = DummyUpdate()
    context = DummyContext()
    asyncio.run(handlers.menu_command(update, context))

    result = captured["result"]
    assert result.actions
