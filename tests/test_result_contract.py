from __future__ import annotations

import asyncio
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from app.bot import actions, handlers
from app.core import calendar_store
from app.core.orchestrator import Orchestrator
from app.core.result import (
    Action,
    OrchestratorResult,
    STRICT_REFUSAL_TEXT,
    ensure_safe_text_strict,
    ensure_valid,
    ok,
    ratelimited,
)
from app.core.tools_calendar import list_calendar_items, list_reminders
from app.infra.rate_limiter import RateLimiter
from app.infra.storage import TaskStorage


class FakeLLMClient:
    def __init__(self) -> None:
        self.api_key = "fake-key"

    async def create_chat_completion(self, *, model: str, messages: list[dict], max_tokens=None, web_search_options=None):
        return {"content": "hello from llm"}

    async def generate_text(self, *, model: str, messages: list[dict], max_tokens=None, web_search_options=None):
        return "hello from llm"


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
    def __init__(self, user_id: int = 1) -> None:
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(id=100)
        self.callback_query = None


def test_result_defaults_empty_lists() -> None:
    result = ok("hi", intent="test", mode="local")
    assert result.sources == []
    assert result.actions == []
    assert result.attachments == []


def test_ratelimited_result_is_valid() -> None:
    result = ratelimited("slow down", intent="rate_limit", mode="local")
    result.validate()


def test_tool_calendar_returns_result(tmp_path, monkeypatch) -> None:
    path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(path))
    calendar_store.save_store_atomic({"events": [], "reminders": [], "updated_at": datetime.now().isoformat()})
    result = asyncio.run(list_calendar_items(None, None))
    assert isinstance(result, OrchestratorResult)
    result.validate()


def test_tool_reminders_returns_result(tmp_path, monkeypatch) -> None:
    path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(path))
    calendar_store.save_store_atomic({"events": [], "reminders": [], "updated_at": datetime.now().isoformat()})
    result = asyncio.run(list_reminders(datetime.now(tz=calendar_store.VIENNA_TZ), limit=5))
    assert isinstance(result, OrchestratorResult)
    result.validate()


def test_orchestrator_local_path_result(tmp_path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    orchestrator = Orchestrator(config={}, storage=storage)

    result = asyncio.run(orchestrator.handle("hello", {"user_id": 1}))

    assert isinstance(result, OrchestratorResult)
    assert result.mode == "local"
    result.validate()


def test_orchestrator_llm_path_result(tmp_path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    orchestrator = Orchestrator(config={}, storage=storage, llm_client=FakeLLMClient())

    result = asyncio.run(orchestrator.handle("Что нового?", {"user_id": 1}))

    assert isinstance(result, OrchestratorResult)
    assert result.mode == "llm"
    result.validate()


def test_orchestrator_error_path_result(tmp_path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    orchestrator = Orchestrator(config={}, storage=storage, llm_client=None)

    result = asyncio.run(orchestrator.handle("Что нового?", {"user_id": 1}))

    assert isinstance(result, OrchestratorResult)
    assert result.status == "error"
    assert "LLM не настроен" in result.text
    result.validate()


def test_handler_does_not_leak_debug(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_safe_send_text(update, context, text, reply_markup=None):
        captured["text"] = text
        captured["reply_markup"] = reply_markup
        return len(text or "")

    monkeypatch.setattr(handlers, "safe_send_text", fake_safe_send_text)

    result = ok("Hello", intent="test", mode="local", debug={"secret": "token"})
    asyncio.run(handlers.send_result(DummyUpdate(), DummyContext(), result))

    assert captured["text"] == "Hello"
    assert "secret" not in str(captured["text"])


def test_handler_renders_actions_keyboard(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_safe_send_text(update, context, text, reply_markup=None):
        captured["reply_markup"] = reply_markup
        return len(text or "")

    monkeypatch.setattr(handlers, "safe_send_text", fake_safe_send_text)

    action = Action(id="test", label="Click me", payload={"foo": "bar"})
    result = ok("Hello", intent="test", mode="local", actions=[action])
    asyncio.run(handlers.send_result(DummyUpdate(), DummyContext(), result))

    reply_markup = captured["reply_markup"]
    assert reply_markup is not None
    assert reply_markup.inline_keyboard[0][0].text == "Click me"
    assert reply_markup.inline_keyboard[0][0].callback_data.startswith("a:")


def test_actions_and_debug_separation() -> None:
    result = ok("Hello", intent="test", mode="local", debug={"actions": "oops"})
    with pytest.raises(ValueError):
        result.validate()
    bad_action = Action(id="test", label="Click", payload={"debug": "oops"})
    result = ok("Hello", intent="test", mode="local", actions=[bad_action])
    with pytest.raises(ValueError):
        result.validate()


def test_attachment_validation() -> None:
    result = ok(
        "Hello",
        intent="test",
        mode="local",
        attachments=[{"type": "image", "name": "sample", "url": "https://example.com"}],
    )
    result.validate()


def test_ensure_valid_none_result() -> None:
    result = ensure_valid(None)
    assert result.status == "error"
    assert result.text is not None
    assert result.sources is not None
    assert result.actions is not None
    assert result.attachments is not None
    assert result.debug is not None


def test_ensure_valid_dict_result_defaults() -> None:
    result = ensure_valid({"status": "ok", "text": "x"})
    assert isinstance(result, OrchestratorResult)
    assert result.status == "ok"
    assert result.text == "x"
    assert result.intent == "unknown"
    assert result.mode == "local"
    assert result.sources == []
    assert result.actions == []
    assert result.attachments == []
    assert result.debug == {}


def test_strict_guard_allows_text_when_sources_present() -> None:
    result = ok("Sources: [1]", intent="test", mode="llm", sources=[{"title": "x", "url": "y", "snippet": "z"}])
    guarded = ensure_safe_text_strict(result, facts_enabled=False, allow_sources_in_text=False)

    assert guarded.status == "ok"
    assert guarded.text == "Sources: [1]"
    assert len(guarded.sources) == 1


def test_ensure_valid_normalizes_none_actions() -> None:
    result = OrchestratorResult(
        text="Hello",
        status="ok",
        mode="local",
        intent="test",
        actions=None,
    )
    normalized = ensure_valid(result)
    assert normalized.actions == []


def test_ensure_valid_preserves_ratelimited_status() -> None:
    result = ratelimited("slow down", intent="rate_limit", mode="local")
    normalized = ensure_valid(result)
    assert normalized.status == "ratelimited"
    assert normalized.text == "slow down"
    assert normalized.intent == "rate_limit"
