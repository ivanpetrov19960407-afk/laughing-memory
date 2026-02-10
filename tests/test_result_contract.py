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
    ensure_safe_text_strict,
    ensure_valid,
    normalize_to_orchestrator_result,
    ok,
    ratelimited,
)
from app.core import tools_calendar_caldav
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
    result = ok("hi", intent="test.example", mode="local")
    assert result.sources == []
    assert result.actions == []
    assert result.attachments == []


def test_ratelimited_result_is_valid() -> None:
    result = ratelimited("slow down", intent="rate.limit", mode="local")
    result.validate()


def test_tool_calendar_returns_result(tmp_path, monkeypatch) -> None:
    path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(path))
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "pass")
    async def fake_list_events(*args, **kwargs):
        return []

    monkeypatch.setattr("app.core.tools_calendar_caldav.list_events", fake_list_events)
    calendar_store.save_store_atomic({"events": [], "reminders": [], "updated_at": datetime.now().isoformat()})
    result = asyncio.run(list_calendar_items(None, None, user_id=1))
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

    result = ok("Hello", intent="test.example", mode="local", debug={"secret": "token"})
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
    result = ok("Hello", intent="test.example", mode="local", actions=[action])
    asyncio.run(handlers.send_result(DummyUpdate(), DummyContext(), result))

    reply_markup = captured["reply_markup"]
    assert reply_markup is not None
    assert reply_markup.inline_keyboard[0][0].text == "Click me"
    assert reply_markup.inline_keyboard[0][0].callback_data.startswith("a:")


def test_actions_and_debug_separation() -> None:
    result = ok("Hello", intent="test.example", mode="local", debug={"actions": "oops"})
    with pytest.raises(ValueError):
        result.validate()
    bad_action = Action(id="test", label="Click", payload={"debug": "oops"})
    result = ok("Hello", intent="test.example", mode="local", actions=[bad_action])
    with pytest.raises(ValueError):
        result.validate()


def test_attachment_validation() -> None:
    result = ok(
        "Hello",
        intent="test.example",
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
    assert result.intent == "unknown.unknown"
    assert result.mode == "local"
    assert result.sources == []
    assert result.actions == []
    assert result.attachments == []
    assert result.debug == {}


def test_normalize_string_result_defaults() -> None:
    result = normalize_to_orchestrator_result("hello")
    assert result.status == "ok"
    assert result.text == "hello"
    assert result.intent == "unknown.unknown"
    assert result.actions == []
    assert result.attachments == []


def test_ensure_valid_moves_extra_fields_to_debug() -> None:
    result = ensure_valid({"status": "ok", "text": "x", "intent": "test.example", "mode": "local", "foo": "bar"})
    assert result.debug.get("extra_fields", {}).get("foo") == "bar"
    assert "actions" not in result.debug


def test_ensure_valid_separates_action_debug() -> None:
    result = ensure_valid(
        {
            "status": "ok",
            "text": "x",
            "intent": "test.example",
            "mode": "local",
            "actions": [{"id": "x", "label": "Open", "payload": {"debug": {"trace": 1}, "foo": "bar"}}],
        }
    )
    assert result.actions[0].payload == {"foo": "bar"}
    assert result.debug.get("action_debug")[0]["debug"] == {"trace": 1}


def test_ensure_valid_invalid_actions_type_moves_to_debug() -> None:
    result = ensure_valid({"status": "ok", "text": "x", "intent": "test.example", "actions": "oops"})
    assert result.actions == []
    assert result.debug.get("invalid_actions") == ["oops"]


def test_strict_guard_allows_text_when_sources_present() -> None:
    result = ok(
        "Sources: [1]",
        intent="test.example",
        mode="llm",
        sources=[{"title": "x", "url": "y", "snippet": "z"}],
    )
    guarded = ensure_safe_text_strict(result, facts_enabled=False, allow_sources_in_text=False)

    assert guarded.status == "ok"
    assert guarded.text == "Sources: [1]"
    assert len(guarded.sources) == 1


def test_ensure_valid_normalizes_none_actions() -> None:
    result = OrchestratorResult(
        text="Hello",
        status="ok",
        mode="local",
        intent="test.example",
        actions=None,
    )
    normalized = ensure_valid(result)
    assert normalized.actions == []


def test_ensure_valid_empty_text_gets_fallback_stub() -> None:
    result = ensure_valid({"status": "ok", "text": "", "intent": "test.example"})
    assert result.text.strip() != ""
    assert "меню" in result.text.lower() or "menu" in result.text.lower()
    assert result.status == "refused"


def test_ensure_valid_refused_preserves_message() -> None:
    msg = "Доступ запрещён."
    result = ensure_valid({"status": "refused", "text": msg, "intent": "auth.refused", "mode": "local"})
    assert result.status == "refused"
    assert result.text == msg
    assert result.intent == "auth.refused"
    assert result.mode == "local"


def test_ensure_valid_actions_validated_and_kept() -> None:
    result = ensure_valid({
        "status": "ok",
        "text": "Done",
        "intent": "test.done",
        "actions": [
            {"id": "a1", "label": "Button", "payload": {"op": "open"}},
        ],
    })
    assert len(result.actions) == 1
    assert result.actions[0].id == "a1"
    assert result.actions[0].label == "Button"
    assert result.actions[0].payload == {"op": "open"}
    result.validate()


def test_ensure_valid_modes_no_conflict() -> None:
    for mode in ("local", "llm", "tool"):
        r = ensure_valid({"status": "ok", "text": "x", "intent": "test.x", "mode": mode})
        assert r.mode == mode
        assert r.status == "ok"


def test_ensure_valid_intent_without_dot_gets_fallback() -> None:
    result = ensure_valid({"status": "ok", "text": "x", "intent": "nodot"})
    assert result.intent == "unknown.unknown"


def test_ensure_valid_intent_with_dot_preserved() -> None:
    result = ensure_valid({"status": "ok", "text": "x", "intent": "wizard.calendar.confirm"})
    assert result.intent == "wizard.calendar.confirm"


def test_ensure_valid_sources_validated_non_empty_url() -> None:
    result = ensure_valid({
        "status": "ok",
        "text": "Answer",
        "intent": "search.result",
        "sources": [
            {"title": "T", "url": "https://example.com", "snippet": "S"},
            {"title": "X", "url": "", "snippet": ""},
        ],
    })
    assert len(result.sources) == 1
    assert result.sources[0].url == "https://example.com"
    assert "invalid_sources" in result.debug or len(result.sources) == 1


def test_ensure_valid_orchestrator_result_instance_preserves_fields() -> None:
    from app.core.result import Source
    src = Source(title="A", url="https://a.com", snippet="x")
    orig = ok("Hello", intent="test.example", mode="tool", sources=[src])
    out = ensure_valid(orig)
    assert out.text == orig.text
    assert out.status == orig.status
    assert out.mode == orig.mode
    assert out.intent == orig.intent
    assert len(out.sources) == 1
    assert out.sources[0].url == src.url
