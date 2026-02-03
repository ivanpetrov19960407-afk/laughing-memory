from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.bot import handlers


class DummyContext:
    def __init__(self) -> None:
        self.application = SimpleNamespace(
            bot_data={
                "settings": SimpleNamespace(enable_menu=True, enable_wizards=False, strict_no_pseudo_sources=True),
                "action_store": SimpleNamespace(),
                "ui_rate_limiter": SimpleNamespace(),
                "rate_limiter": SimpleNamespace(),
                "orchestrator": SimpleNamespace(is_facts_only=lambda user_id: False),
            }
        )
        self.chat_data: dict[str, object] = {}


class DummyUpdate:
    def __init__(self) -> None:
        self.effective_user = SimpleNamespace(id=1, username="tester")
        self.effective_chat = SimpleNamespace(id=10)
        self.message = SimpleNamespace(text="/unknown")
        self.effective_message = self.message
        self.callback_query = None


def test_unknown_command_returns_refused(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        return True

    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    update = DummyUpdate()
    context = DummyContext()
    asyncio.run(handlers.unknown_command(update, context))

    result = captured["result"]
    assert result.status == "refused"
    assert "Неизвестная команда" in result.text
