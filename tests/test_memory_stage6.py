from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.core.dialog_memory import DialogMemory
from app.core.memory_layers import build_memory_layers_context
from app.core.memory_manager import MemoryManager, UserProfileMemory
from app.infra.actions_log_store import ActionsLogStore
from app.infra.request_context import RequestContext
from app.infra.user_profile_store import UserProfileStore


def test_dialog_memory_ttl_and_clear(tmp_path) -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def now_provider() -> datetime:
        return now

    memory = DialogMemory(tmp_path / "dialog.json", max_turns=5, ttl_seconds=10, now_provider=now_provider)
    asyncio.run(memory.load())
    asyncio.run(memory.add_user(1, 2, "Первое сообщение"))
    now += timedelta(seconds=11)
    asyncio.run(memory.add_user(1, 2, "Второе сообщение"))

    messages = asyncio.run(memory.get_context(1, 2))
    assert [message.text for message in messages] == ["Второе сообщение"]

    asyncio.run(memory.clear(1, 2))
    messages = asyncio.run(memory.get_context(1, 2))
    assert messages == []


def test_user_profile_persistence(tmp_path) -> None:
    store = UserProfileStore(tmp_path / "profiles.db")
    store.update(1, {"language": "en", "timezone": "Europe/London", "facts_mode_default": True})

    reloaded = UserProfileStore(tmp_path / "profiles.db")
    profile = reloaded.get(1)

    assert profile.language == "en"
    assert profile.timezone == "Europe/London"
    assert profile.facts_mode_default is True


def test_actions_log_records_events(tmp_path) -> None:
    store = ActionsLogStore(tmp_path / "actions.db")
    entry = store.append(
        user_id=1,
        action_type="reminder.create",
        payload={"summary": "Создал напоминание"},
        ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    entries = store.search(user_id=1, query="reminder", limit=5)

    assert entry.action_type == "reminder.create"
    assert entries
    assert entries[0].action_type == "reminder.create"
    assert entries[0].payload["summary"] == "Создал напоминание"


def test_memory_toggle_affects_llm_context(tmp_path) -> None:
    dialog_memory = DialogMemory(tmp_path / "dialog.json", max_turns=5)
    asyncio.run(dialog_memory.load())
    asyncio.run(dialog_memory.add_user(1, 2, "Напомни про отчёт"))
    asyncio.run(dialog_memory.set_enabled(1, False))

    request_context = RequestContext(
        correlation_id="corr-1",
        user_id=1,
        chat_id=2,
        message_id=3,
        timezone=None,
        ts=datetime.now(timezone.utc),
        env="dev",
    )
    memory_manager = MemoryManager(dialog=dialog_memory, profile=None, actions=None)

    context_disabled = asyncio.run(
        build_memory_layers_context(request_context, memory_manager=memory_manager, max_chars=2000)
    )

    asyncio.run(dialog_memory.set_enabled(1, True))
    context_enabled = asyncio.run(
        build_memory_layers_context(request_context, memory_manager=memory_manager, max_chars=2000)
    )

    assert context_disabled is None
    assert context_enabled is not None
    assert "Напомни про отчёт" in context_enabled


def test_memory_clear_does_not_touch_profile(tmp_path) -> None:
    dialog_memory = DialogMemory(tmp_path / "dialog.json", max_turns=5)
    asyncio.run(dialog_memory.load())
    asyncio.run(dialog_memory.set_enabled(1, True))
    asyncio.run(dialog_memory.add_user(1, 2, "Тест"))

    profile_store = UserProfileStore(tmp_path / "profiles.db")
    profile_store.update(1, {"language": "en", "timezone": "Europe/London"})
    memory_manager = MemoryManager(
        dialog=dialog_memory,
        profile=UserProfileMemory(profile_store),
        actions=None,
    )

    asyncio.run(memory_manager.clear_dialog(1, 2))
    profile = memory_manager.get_profile(1)

    assert profile is not None
    assert profile.language == "en"
    assert profile.timezone == "Europe/London"
