from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.actions_log import ActionLogEntry
from app.core.dialog_memory import DialogMemory, DialogMessage
from app.core.memory_store import MemoryStore, build_llm_context
from app.core.user_profile import UserProfile
from app.infra.actions_log_store import ActionsLogStore
from app.infra.request_context import RequestContext
from app.infra.user_profile_store import UserProfileStore


@dataclass(frozen=True)
class DialogContextLayer:
    memory: DialogMemory

    async def get_context(self, user_id: int, chat_id: int) -> list[DialogMessage]:
        return await self.memory.get_context(user_id, chat_id)

    async def save_message(self, user_id: int, chat_id: int, role: str, text: str) -> None:
        if role == "user":
            await self.memory.add_user(user_id, chat_id, text)
        else:
            await self.memory.add_assistant(user_id, chat_id, text)


@dataclass(frozen=True)
class UserProfileLayer:
    store: UserProfileStore

    def get(self, user_id: int) -> UserProfile:
        return self.store.get(user_id)

    def update(self, user_id: int, patch: dict[str, Any]) -> UserProfile:
        return self.store.update(user_id, patch)

    def remember(self, user_id: int, text: str) -> UserProfile:
        return self.store.add_note(user_id, text)

    def forget(self, user_id: int, key: str) -> tuple[UserProfile, bool]:
        return self.store.remove_note(user_id, key)


@dataclass(frozen=True)
class ActionsLogLayer:
    store: ActionsLogStore

    def append(
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

    def search(self, user_id: int, query: str | None, limit: int) -> list[ActionLogEntry]:
        return self.store.search(user_id=user_id, query=query, limit=limit)


@dataclass(frozen=True)
class MemoryLayers:
    dialog: DialogContextLayer | None
    profile: UserProfileLayer | None
    actions: ActionsLogLayer | None


def build_memory_layers_context(
    request_context: RequestContext | None,
    *,
    memory_store: MemoryStore | None,
    profile_layer: UserProfileLayer | None,
    actions_layer: ActionsLogLayer | None,
    max_chars: int = 2000,
    action_limit: int = 5,
    action_days: int = 7,
) -> str | None:
    if request_context is None:
        return None
    user_id = int(request_context.user_id or 0)
    chat_id = int(request_context.chat_id or 0)
    blocks: list[str] = []
    memory_context = build_llm_context(request_context, memory_store, limit=10, max_chars=max_chars)
    if memory_context:
        blocks.append(memory_context)
    if profile_layer is not None and user_id:
        profile = profile_layer.get(user_id)
        profile_text = _render_profile(profile, max_chars=max_chars)
        if profile_text:
            blocks.append(profile_text)
    if actions_layer is not None and user_id:
        actions_text = _render_actions(
            actions_layer,
            user_id=user_id,
            now=request_context.ts or datetime.now(timezone.utc),
            limit=action_limit,
            max_chars=max_chars,
            days=action_days,
        )
        if actions_text:
            blocks.append(actions_text)
    if not blocks:
        return None
    combined = "\n\n".join(blocks).strip()
    if len(combined) <= max_chars:
        return combined
    return combined[: max(1, max_chars - 1)].rstrip() + "…"


def _render_profile(profile: UserProfile, *, max_chars: int) -> str:
    notes = profile.notes[:5]
    reminder_defaults = profile.default_reminders
    note_lines = [f"• {note.text} (id: {note.id})" for note in notes]
    reminder_offset = (
        f"{reminder_defaults.offset_minutes} минут"
        if reminder_defaults.offset_minutes is not None
        else "не задано"
    )
    facts_label = "вкл" if profile.facts_mode_default else "выкл"
    reminders_label = "вкл" if reminder_defaults.enabled else "выкл"
    blocks = [
        "Профиль пользователя:",
        f"• язык: {profile.language}",
        f"• таймзона: {profile.timezone}",
        f"• подробность: {profile.verbosity}",
        f"• режим фактов по умолчанию: {facts_label}",
        f"• напоминания по умолчанию: {reminders_label}",
        f"• смещение напоминаний: {reminder_offset}",
    ]
    if profile.style:
        blocks.append(f"• стиль: {profile.style}")
    if note_lines:
        blocks.append("Заметки:")
        blocks.extend(note_lines)
    rendered = "\n".join(blocks)
    if len(rendered) <= max_chars:
        return rendered
    return rendered[: max(1, max_chars - 1)].rstrip() + "…"


def _render_actions(
    actions_layer: ActionsLogLayer,
    *,
    user_id: int,
    now: datetime,
    limit: int,
    max_chars: int,
    days: int,
) -> str | None:
    entries = actions_layer.search(user_id, query=None, limit=max(1, limit))
    if not entries:
        return None
    cutoff = now - timedelta(days=max(1, days))
    recent = [
        entry
        for entry in entries
        if entry.ts >= cutoff and entry.action_type.startswith(("calendar.", "reminder."))
    ]
    if not recent:
        return None
    recent = recent[: max(1, limit)]

    def _render(entries_to_render: list[ActionLogEntry]) -> str:
        lines = []
        for entry in entries_to_render:
            timestamp = entry.ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
            summary = entry.to_summary()
            lines.append(f"• {timestamp} | {entry.action_type} | {summary}")
        return "Последние действия:\n" + "\n".join(lines)

    block = _render(recent)
    while len(block) > max_chars and len(recent) > 1:
        recent = recent[:-1]
        block = _render(recent)
    if len(block) <= max_chars:
        return block
    return block[: max(1, max_chars - 1)].rstrip() + "…"
