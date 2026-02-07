from __future__ import annotations

from datetime import datetime, timezone, timedelta

from app.core.memory_layers import ActionsLogLayer, UserProfileLayer, build_memory_layers_context
from app.core.memory_store import MemoryStore
from app.infra.actions_log_store import ActionsLogStore
from app.infra.request_context import RequestContext
from app.infra.user_profile_store import UserProfileStore


def test_memory_layers_context_includes_profile_and_actions(tmp_path) -> None:
    memory_store = MemoryStore(max_items=5, ttl_seconds=3600)
    profile_store = UserProfileStore(tmp_path / "profiles.db")
    actions_store = ActionsLogStore(tmp_path / "actions.db")

    profile_store.update(1, {"language": "en", "timezone": "Europe/London", "verbosity": "short"})
    actions_store.append(
        user_id=1,
        action_type="calendar.create",
        payload={"summary": "Создал событие"},
        ts=datetime.now(timezone.utc),
    )
    actions_store.append(
        user_id=1,
        action_type="calendar.delete",
        payload={"summary": "Старое событие"},
        ts=datetime.now(timezone.utc) - timedelta(days=10),
    )
    memory_store.add(
        chat_id=2,
        user_id=1,
        role="user",
        kind="message",
        content="Напомни о встрече",
        env="dev",
        correlation_id="old",
    )

    request_context = RequestContext(
        correlation_id="corr-1",
        user_id=1,
        chat_id=2,
        message_id=3,
        timezone=None,
        ts=datetime.now(timezone.utc),
        env="dev",
    )
    context = build_memory_layers_context(
        request_context,
        memory_store=memory_store,
        profile_layer=UserProfileLayer(profile_store),
        actions_layer=ActionsLogLayer(actions_store),
        max_chars=2000,
    )

    assert context is not None
    assert "Профиль пользователя" in context
    assert "Europe/London" in context
    assert "Последние действия" in context
    assert "Создал событие" in context
    assert "Старое событие" not in context
