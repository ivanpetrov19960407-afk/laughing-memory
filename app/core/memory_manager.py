from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.actions_log import ActionLogEntry
from app.core.dialog_memory import DialogMemory, DialogMessage, DialogRole
from app.core.user_profile import UserProfile, default_profile
from app.infra.actions_log_store import ActionsLogStore
from app.infra.user_profile_store import UserProfileStore

PREF_KEYS = (
    "language",
    "verbosity",
    "facts_mode_default",
    "context_default",
    "timezone",
    "date_format",
    "actions_log_enabled",
    "default_reminders",
    "style",
)


@dataclass(frozen=True)
class UserProfileMemory:
    store: UserProfileStore

    def get(self, user_id: int) -> UserProfile:
        return self.store.get(user_id)

    def set(self, user_id: int, patch: dict[str, Any]) -> UserProfile:
        return self.store.update(user_id, patch)

    def clear(self, user_id: int) -> UserProfile:
        profile = default_profile(user_id)
        self.store.set_defaults(user_id, profile)
        return profile

    def list(self, user_id: int) -> UserProfile:
        return self.get(user_id)

    def is_persisted(self, user_id: int) -> bool:
        return self.store.exists(user_id)


@dataclass(frozen=True)
class UserActionsLog:
    store: ActionsLogStore

    def get(self, user_id: int, query: str | None = None, limit: int = 10) -> list[ActionLogEntry]:
        return self.store.search(user_id=user_id, query=query, limit=limit)

    def set(
        self,
        user_id: int,
        action_type: str,
        payload: dict[str, Any],
        ts: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ActionLogEntry:
        return self.store.append(
            user_id=user_id,
            action_type=action_type,
            payload=payload,
            ts=ts,
            correlation_id=correlation_id,
        )

    def clear(self, user_id: int) -> None:
        self.store.clear(user_id=user_id)

    def list(self, user_id: int, limit: int = 10, since: datetime | None = None) -> list[ActionLogEntry]:
        return self.store.list(user_id=user_id, limit=limit, since=since)


@dataclass(frozen=True)
class MemoryManager:
    dialog: DialogMemory | None
    profile: UserProfileMemory | None
    actions: UserActionsLog | None

    async def dialog_enabled(self, user_id: int) -> bool:
        if self.dialog is None:
            return False
        return await self.dialog.is_enabled(user_id)

    async def set_dialog_enabled(self, user_id: int, enabled: bool) -> None:
        if self.dialog is None:
            return
        await self.dialog.set_enabled(user_id, enabled)

    async def clear_dialog(self, user_id: int, chat_id: int) -> None:
        if self.dialog is None:
            return
        await self.dialog.clear(user_id, chat_id)

    async def add_dialog_message(
        self,
        user_id: int,
        chat_id: int,
        role: DialogRole,
        text: str,
    ) -> None:
        if self.dialog is None:
            return
        await self.dialog.set(user_id, chat_id, role, text)

    async def get_dialog(self, user_id: int, chat_id: int, *, limit: int | None = None) -> list[DialogMessage]:
        if self.dialog is None:
            return []
        return await self.dialog.list(user_id, chat_id, limit=limit)

    async def dialog_status(self, user_id: int, chat_id: int) -> tuple[bool, int]:
        if self.dialog is None:
            return False, 0
        return await self.dialog.get_status(user_id, chat_id)

    def get_profile(self, user_id: int) -> UserProfile | None:
        if self.profile is None:
            return None
        return self.profile.get(user_id)

    def update_profile(self, user_id: int, patch: dict[str, Any]) -> UserProfile | None:
        if self.profile is None:
            return None
        return self.profile.set(user_id, patch)

    def remember_profile(self, user_id: int, text: str) -> UserProfile | None:
        if self.profile is None:
            return None
        return self.profile.store.add_note(user_id, text)

    def forget_profile(self, user_id: int, key: str) -> tuple[UserProfile, bool] | None:
        if self.profile is None:
            return None
        return self.profile.store.remove_note(user_id, key)

    def clear_profile(self, user_id: int) -> UserProfile | None:
        if self.profile is None:
            return None
        return self.profile.clear(user_id)

    def list_profile(self, user_id: int) -> UserProfile | None:
        return self.get_profile(user_id)

    def profile_is_persisted(self, user_id: int) -> bool:
        if self.profile is None:
            return False
        return self.profile.is_persisted(user_id)

    def get_user_prefs(self, user_id: int) -> dict[str, Any]:
        if self.profile is None:
            return {}
        profile = self.profile.get(user_id)
        raw = profile.to_dict()
        return {k: raw[k] for k in PREF_KEYS if k in raw}

    def set_user_pref(self, user_id: int, key: str, value: Any) -> UserProfile | None:
        if self.profile is None:
            return None
        return self.profile.set(user_id, {key: value})

    def actions_log_enabled(self, user_id: int) -> bool:
        if self.profile is None:
            return True
        profile = self.profile.get(user_id)
        return profile.actions_log_enabled

    def set_actions_log_enabled(self, user_id: int, enabled: bool) -> None:
        if self.profile is not None:
            self.profile.set(user_id, {"actions_log_enabled": enabled})

    def log_user_action(
        self,
        user_id: int,
        action_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> None:
        if not user_id or self.actions is None:
            return
        if not self.actions_log_enabled(user_id):
            return
        self.actions.set(
            user_id=user_id,
            action_type=action_type,
            payload=payload,
            correlation_id=correlation_id,
        )
