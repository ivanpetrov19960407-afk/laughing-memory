from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core.dialog_memory import DialogMemory
from app.core.memory_layers import build_memory_layers_context
from app.core.memory_manager import MemoryManager, UserProfileMemory
from app.infra.request_context import RequestContext
from app.infra.user_profile_store import UserProfileStore


def test_memory_layers_context_includes_dialog_and_profile(tmp_path) -> None:
    dialog_memory = DialogMemory(tmp_path / "dialog.json", max_turns=5)
    asyncio.run(dialog_memory.load())
    asyncio.run(dialog_memory.set_enabled(1, True))
    asyncio.run(dialog_memory.add_user(1, 2, "Напомни о встрече"))
    profile_store = UserProfileStore(tmp_path / "profiles.db")
    profile_store.update(1, {"language": "en", "facts_mode_default": True, "style": "formal"})

    request_context = RequestContext(
        correlation_id="corr-1",
        user_id=1,
        chat_id=2,
        message_id=3,
        timezone=None,
        ts=datetime.now(timezone.utc),
        env="dev",
    )
    memory_manager = MemoryManager(
        dialog=dialog_memory,
        profile=UserProfileMemory(profile_store),
        actions=None,
    )
    context = asyncio.run(
        build_memory_layers_context(
            request_context,
            memory_manager=memory_manager,
            max_chars=2000,
        )
    )

    assert context is not None
    assert "Профиль пользователя" in context
    assert "язык: en" in context
    assert "режим фактов: вкл" in context
    assert "formal" in context


def test_memory_layers_context_truncates_dialog(tmp_path) -> None:
    dialog_memory = DialogMemory(tmp_path / "dialog.json", max_turns=5)
    asyncio.run(dialog_memory.load())
    asyncio.run(dialog_memory.set_enabled(1, True))
    for _ in range(3):
        asyncio.run(dialog_memory.add_user(1, 2, "Очень длинное сообщение " * 10))
    request_context = RequestContext(
        correlation_id="corr-2",
        user_id=1,
        chat_id=2,
        message_id=3,
        timezone=None,
        ts=datetime.now(timezone.utc),
        env="dev",
    )
    memory_manager = MemoryManager(dialog=dialog_memory, profile=None, actions=None)
    context = asyncio.run(
        build_memory_layers_context(
            request_context,
            memory_manager=memory_manager,
            max_chars=120,
        )
    )

    assert context is not None
    assert len(context) <= 120
