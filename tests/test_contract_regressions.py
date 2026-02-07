from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.bot import actions, handlers
from app.core import calendar_store
from app.core.result import Action, ensure_valid, refused
from app.infra.rate_limiter import RateLimiter


class DummyContext:
    def __init__(self) -> None:
        self.application = SimpleNamespace(
            bot_data={
                "action_store": actions.ActionStore(),
                "ui_rate_limiter": RateLimiter(),
                "rate_limiter": RateLimiter(),
                "settings": SimpleNamespace(enable_menu=True, enable_wizards=False, strict_no_pseudo_sources=True),
            }
        )
        self.chat_data: dict[str, object] = {}


class DummyUpdate:
    def __init__(self, text: str = "/unknown") -> None:
        self.effective_user = SimpleNamespace(id=1, username="tester")
        self.effective_chat = SimpleNamespace(id=10)
        self.message = SimpleNamespace(text=text)
        self.effective_message = self.message
        self.callback_query = None


def test_unknown_command_fallback(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        return True

    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    asyncio.run(handlers.unknown_command(DummyUpdate(), DummyContext()))
    result = captured["result"]
    assert result.status == "refused"
    assert result.text.strip()


def test_refused_text_is_sent(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_safe_send_text(update, context, text, reply_markup=None):
        captured["text"] = text

    monkeypatch.setattr(handlers, "safe_send_text", fake_safe_send_text)

    result = refused("Отказ с текстом", intent="test.refused", mode="local")
    asyncio.run(handlers.send_result(DummyUpdate(), DummyContext(), result))
    assert captured["text"] == "Отказ с текстом"


def test_no_pseudo_sources_when_sources_empty() -> None:
    result = ensure_valid({"status": "ok", "text": "Согласно [1] источники: https://example.com", "intent": "test"})
    lowered = result.text.lower()
    assert "[1]" not in result.text
    assert "источники" not in lowered
    assert "согласно" not in lowered
    assert "https://" not in lowered


def test_actions_rendering_contract(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_safe_send_text(update, context, text, reply_markup=None):
        captured["markup"] = reply_markup

    monkeypatch.setattr(handlers, "safe_send_text", fake_safe_send_text)

    result = ensure_valid(
        {
            "status": "ok",
            "text": "menu",
            "intent": "test.actions",
            "actions": [{"id": "x", "label": "Open", "payload": {"op": "menu_open"}}],
        }
    )
    asyncio.run(handlers.send_result(DummyUpdate(), DummyContext(), result))
    assert isinstance(result.actions[0], Action)
    assert captured["markup"] is not None
    assert captured["markup"].inline_keyboard[0][0].text == "Open"


def test_datetime_timezone_vilnius() -> None:
    assert str(calendar_store.BOT_TZ) == "Europe/Moscow"
    parsed = calendar_store.parse_user_datetime("2026-02-05 10:00")
    assert parsed.tzinfo is not None
    assert parsed.tzinfo == calendar_store.BOT_TZ
