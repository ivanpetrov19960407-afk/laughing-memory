from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.bot import actions, handlers
from app.core.memory_store import MemoryStore
from app.core.result import ok
from app.infra.actions_log_store import ActionsLogStore
from app.infra.rate_limiter import RateLimiter
from app.infra.request_context import start_request
from app.infra.user_profile_store import UserProfileStore


class DummyOrchestrator:
    def is_facts_only(self, user_id: int) -> bool:
        return False


class DummyContext:
    def __init__(self, profile_store: UserProfileStore, actions_store: ActionsLogStore) -> None:
        self.application = SimpleNamespace(
            bot_data={
                "orchestrator": DummyOrchestrator(),
                "action_store": actions.ActionStore(),
                "rate_limiter": RateLimiter(),
                "ui_rate_limiter": RateLimiter(),
                "settings": SimpleNamespace(enable_menu=True, enable_wizards=True, strict_no_pseudo_sources=False),
                "profile_store": profile_store,
                "actions_log_store": actions_store,
                "memory_store": MemoryStore(),
            }
        )
        self.chat_data: dict[str, object] = {}
        self.args: list[str] = []


class DummyUpdate:
    def __init__(self, text: str = "/profile", user_id: int = 1, chat_id: int = 10) -> None:
        self.effective_user = SimpleNamespace(id=user_id, username="tester")
        self.effective_chat = SimpleNamespace(id=chat_id, type="private")
        message = SimpleNamespace(text=text, caption=None)
        self.message = message
        self.effective_message = message
        self.callback_query = None


def test_profile_command_renders_profile(monkeypatch, tmp_path) -> None:
    sent: list[str] = []

    async def fake_send_text(update, context, text, reply_markup=None):
        sent.append(text)
        return len(text or "")

    async def fake_guard_access(update, context, bucket="default"):
        return True

    monkeypatch.setattr(handlers, "safe_send_text", fake_send_text)
    monkeypatch.setattr(handlers, "safe_edit_text", fake_send_text)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    profile_store = UserProfileStore(tmp_path / "profiles.db")
    actions_store = ActionsLogStore(tmp_path / "actions.db")
    context = DummyContext(profile_store, actions_store)
    update = DummyUpdate(text="/profile")

    asyncio.run(handlers.profile_command(update, context))
    assert "Профиль пользователя" in sent[-1]


def test_history_command_lists_actions(monkeypatch, tmp_path) -> None:
    sent: list[str] = []

    async def fake_send_text(update, context, text, reply_markup=None):
        sent.append(text)
        return len(text or "")

    async def fake_guard_access(update, context, bucket="default"):
        return True

    monkeypatch.setattr(handlers, "safe_send_text", fake_send_text)
    monkeypatch.setattr(handlers, "safe_edit_text", fake_send_text)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    profile_store = UserProfileStore(tmp_path / "profiles.db")
    actions_store = ActionsLogStore(tmp_path / "actions.db")
    actions_store.append(user_id=1, action_type="calendar.event.create", payload={"summary": "Создал событие"})
    context = DummyContext(profile_store, actions_store)
    update = DummyUpdate(text="/history")

    asyncio.run(handlers.history_command(update, context))
    assert "Последние действия" in sent[-1]
    assert "Создал событие" in sent[-1]


def test_send_result_logs_actions(monkeypatch, tmp_path) -> None:
    async def fake_send_text(update, context, text, reply_markup=None):
        return len(text or "")

    monkeypatch.setattr(handlers, "safe_send_text", fake_send_text)
    monkeypatch.setattr(handlers, "safe_edit_text", fake_send_text)

    profile_store = UserProfileStore(tmp_path / "profiles.db")
    actions_store = ActionsLogStore(tmp_path / "actions.db")
    context = DummyContext(profile_store, actions_store)
    update = DummyUpdate(text="/anything")
    start_request(update, context)

    asyncio.run(handlers.send_result(update, context, ok("ok", intent="utility_reminders.create", mode="local")))

    entries = actions_store.search(user_id=1, query=None, limit=10)
    assert entries
    assert entries[0].action_type == "reminder.create"


def test_history_after_action(monkeypatch, tmp_path) -> None:
    sent: list[str] = []

    async def fake_send_text(update, context, text, reply_markup=None):
        sent.append(text)
        return len(text or "")

    async def fake_guard_access(update, context, bucket="default"):
        return True

    monkeypatch.setattr(handlers, "safe_send_text", fake_send_text)
    monkeypatch.setattr(handlers, "safe_edit_text", fake_send_text)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    profile_store = UserProfileStore(tmp_path / "profiles.db")
    actions_store = ActionsLogStore(tmp_path / "actions.db")
    context = DummyContext(profile_store, actions_store)
    update = DummyUpdate(text="/anything")
    start_request(update, context)

    asyncio.run(handlers.send_result(update, context, ok("ок", intent="utility_reminders.create", mode="local")))

    sent.clear()
    update = DummyUpdate(text="/history")
    asyncio.run(handlers.history_command(update, context))
    assert "Последние действия" in sent[-1]
    assert "reminder.create" in sent[-1]
