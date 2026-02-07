from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.bot import actions, handlers, wizard
from app.core.memory_store import MemoryStore
from app.infra.actions_log_store import ActionsLogStore
from app.infra.rate_limiter import RateLimiter
from app.infra.user_profile_store import UserProfileStore
from app.storage.wizard_store import WizardStore


class DummyOrchestrator:
    def is_facts_only(self, user_id: int) -> bool:
        return False


class DummyContext:
    def __init__(
        self,
        profile_store: UserProfileStore,
        actions_store: ActionsLogStore,
        manager: wizard.WizardManager,
    ) -> None:
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
                "wizard_manager": manager,
            }
        )
        self.chat_data: dict[str, object] = {}
        self.args: list[str] = []


class DummyUpdate:
    def __init__(self, text: str = "/profile_set", user_id: int = 1, chat_id: int = 10) -> None:
        self.effective_user = SimpleNamespace(id=user_id, username="tester")
        self.effective_chat = SimpleNamespace(id=chat_id, type="private")
        message = SimpleNamespace(text=text, caption=None)
        self.message = message
        self.effective_message = message
        self.callback_query = None


def test_profile_set_wizard_updates_profile(monkeypatch, tmp_path) -> None:
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
    wizard_store = WizardStore(tmp_path / "wizards")
    manager = wizard.WizardManager(wizard_store, profile_store=profile_store)
    context = DummyContext(profile_store, actions_store, manager)

    update = DummyUpdate(text="/profile_set")
    asyncio.run(handlers.profile_set_command(update, context))
    assert "Выбери язык" in sent[-1]

    result = asyncio.run(manager.handle_text(user_id=1, chat_id=10, text="ru"))
    assert result is not None
    assert "таймзону" in result.text.lower()

    result = asyncio.run(manager.handle_text(user_id=1, chat_id=10, text="Europe/Vilnius"))
    assert result is not None
    assert "подробно" in result.text.lower()

    result = asyncio.run(manager.handle_text(user_id=1, chat_id=10, text="short"))
    assert result is not None
    assert "напоминания" in result.text.lower()

    result = asyncio.run(manager.handle_text(user_id=1, chat_id=10, text="да"))
    assert result is not None
    assert "сколько минут" in result.text.lower()

    result = asyncio.run(manager.handle_text(user_id=1, chat_id=10, text="30"))
    assert result is not None
    assert "Профиль обновлён" in result.text

    sent.clear()
    update = DummyUpdate(text="/profile")
    asyncio.run(handlers.profile_command(update, context))
    assert "таймзона: Europe/Vilnius" in sent[-1]
    assert "подробность: short" in sent[-1]
    assert "напоминания по умолчанию: вкл" in sent[-1]
    assert "смещение напоминаний: 30 минут" in sent[-1]
