"""ActionStore and inline keyboard building for Telegram.

Stores Action payloads under short tokens (callback_data limit 64 bytes);
build_inline_keyboard() turns OrchestratorResult.actions into InlineKeyboardMarkup.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.result import Action

LOGGER = logging.getLogger(__name__)

CALLBACK_PREFIX = "a:"
STATIC_CALLBACK_PREFIX = "cb:"
DEFAULT_TTL_SECONDS = 900


@dataclass
class StoredAction:
    """One stored action: user/chat, intent, payload, TTL."""

    user_id: int
    chat_id: int
    intent: str
    payload: dict[str, Any]
    created_at: float
    expires_at: float


@dataclass
class ActionLookup:
    """Result of looking up a callback token: action (if found), status, age/ttl."""

    action: StoredAction | None
    status: str
    age_seconds: float | None
    ttl_seconds: float | None


class ActionStore:
    """In-memory store for action payloads keyed by short tokens for callback_data."""

    def __init__(
        self,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_items: int = 2000,
        max_payload_bytes: int = 2048,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_items = max_items
        self._max_payload_bytes = max_payload_bytes
        self._items: dict[str, StoredAction] = {}

    def store_action(self, *, action: Action, user_id: int, chat_id: int) -> str:
        """Store action for user/chat; returns short token for callback_data."""
        self._cleanup()
        payload = action.payload or {}
        self._validate_payload(payload)
        action_id = self._generate_token()
        now = time.monotonic()
        self._items[action_id] = StoredAction(
            user_id=user_id,
            chat_id=chat_id,
            intent=action.id,
            payload=payload,
            created_at=now,
            expires_at=now + self._ttl_seconds,
        )
        return action_id

    def get_action(self, *, user_id: int, chat_id: int, action_id: str) -> StoredAction | None:
        self._cleanup()
        item = self._items.get(action_id)
        if item is None or item.user_id != user_id or item.chat_id != chat_id:
            return None
        if item.expires_at < time.monotonic():
            self._items.pop(action_id, None)
            return None
        return item

    def lookup_action(self, *, user_id: int, chat_id: int, action_id: str) -> ActionLookup:
        self._cleanup()
        now = time.monotonic()
        item = self._items.get(action_id)
        if item is None:
            return ActionLookup(action=None, status="missing", age_seconds=None, ttl_seconds=self._ttl_seconds)
        if item.user_id != user_id or item.chat_id != chat_id:
            return ActionLookup(action=None, status="mismatch", age_seconds=None, ttl_seconds=self._ttl_seconds)
        age = now - item.created_at
        ttl = item.expires_at - item.created_at
        if item.expires_at < now:
            self._items.pop(action_id, None)
            return ActionLookup(action=None, status="expired", age_seconds=age, ttl_seconds=ttl)
        return ActionLookup(action=item, status="ok", age_seconds=age, ttl_seconds=ttl)

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > self._max_payload_bytes:
            raise ValueError("Action payload is too large")

    def _generate_token(self) -> str:
        for _ in range(5):
            token = secrets.token_urlsafe(8)
            if token not in self._items:
                return token
        return secrets.token_urlsafe(12)

    def _cleanup(self) -> None:
        now = time.monotonic()
        expired = [token for token, item in self._items.items() if item.expires_at < now]
        for token in expired:
            self._items.pop(token, None)
        if len(self._items) > self._max_items:
            overage = len(self._items) - self._max_items
            items_by_age = sorted(self._items.items(), key=lambda entry: entry[1].created_at)
            for token, _item in items_by_age[:overage]:
                self._items.pop(token, None)


def parse_callback_token(data: str | None) -> str | None:
    """Extract stored-action token from callback_data if it starts with CALLBACK_PREFIX."""
    if not data:
        return None
    if not data.startswith(CALLBACK_PREFIX):
        return None
    return data[len(CALLBACK_PREFIX) :]


def build_static_callback_data(action: Action) -> str | None:
    payload = action.payload or {}
    op = payload.get("op")
    if not isinstance(op, str):
        return None
    if op == "menu_open":
        return f"{STATIC_CALLBACK_PREFIX}menu:open"
    if op == "menu_cancel":
        return f"{STATIC_CALLBACK_PREFIX}menu:cancel"
    if op == "menu_section":
        section = payload.get("section")
        if isinstance(section, str) and section:
            return f"{STATIC_CALLBACK_PREFIX}menu:section:{section}"
        return None
    wizard_ops = {
        "wizard_confirm": "confirm",
        "wizard_cancel": "cancel",
        "wizard_edit": "edit",
        "wizard_continue": "continue",
        "wizard_restart": "restart",
        "wizard_start": "start",
    }
    if op in wizard_ops:
        suffix = wizard_ops[op]
        callback = f"{STATIC_CALLBACK_PREFIX}wiz:{suffix}"
        wizard_id = payload.get("wizard_id")
        if suffix in {"start", "continue", "restart"}:
            if not isinstance(wizard_id, str) or not wizard_id:
                return None
            return f"{callback}:{wizard_id}"
        if isinstance(wizard_id, str) and wizard_id:
            return f"{callback}:{wizard_id}"
        return callback
    # Reminder callbacks must be static, predictable and contain no user text.
    # Format: cb:rem:<action>:... where all parts are strict tokens/ids.
    if op == "reminder_snooze":
        reminder_id = payload.get("reminder_id") or payload.get("id")
        minutes = payload.get("minutes")
        if not isinstance(reminder_id, str) or not reminder_id:
            return None
        if not isinstance(minutes, int) or minutes < 1:
            return None
        return f"{STATIC_CALLBACK_PREFIX}rem:s:{minutes}:{reminder_id}"
    if op == "reminder_snooze_now":
        reminder_id = payload.get("reminder_id") or payload.get("id")
        minutes = payload.get("minutes")
        if not isinstance(reminder_id, str) or not reminder_id:
            return None
        if not isinstance(minutes, int) or minutes < 1:
            return None
        return f"{STATIC_CALLBACK_PREFIX}rem:sn:{minutes}:{reminder_id}"
    if op == "reminder_reschedule":
        reminder_id = payload.get("reminder_id") or payload.get("id")
        if not isinstance(reminder_id, str) or not reminder_id:
            return None
        return f"{STATIC_CALLBACK_PREFIX}rem:r:{reminder_id}"
    if op == "reminder.delete_confirm":
        reminder_id = payload.get("reminder_id") or payload.get("id")
        if not isinstance(reminder_id, str) or not reminder_id:
            return None
        return f"{STATIC_CALLBACK_PREFIX}rem:dc:{reminder_id}"
    if op == "reminder.delete_confirmed":
        reminder_id = payload.get("reminder_id") or payload.get("id")
        if not isinstance(reminder_id, str) or not reminder_id:
            return None
        return f"{STATIC_CALLBACK_PREFIX}rem:dd:{reminder_id}"
    # Daily digest toggle (per-user), stored in profile.
    if op == "digest_toggle":
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            return None
        return f"{STATIC_CALLBACK_PREFIX}digest:{'on' if enabled else 'off'}"
    return None


def build_inline_keyboard(
    actions: list[Action],
    *,
    store: ActionStore,
    user_id: int,
    chat_id: int,
    columns: int = 2,
) -> InlineKeyboardMarkup | None:
    """Build Telegram InlineKeyboardMarkup from actions; dynamic ones go through store."""
    if not actions:
        return None
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for index, action in enumerate(actions, start=1):
        data = build_static_callback_data(action)
        if data is None:
            try:
                action_id = store.store_action(action=action, user_id=user_id, chat_id=chat_id)
            except ValueError:
                LOGGER.warning("Action payload too large: action_id=%s", action.id)
                continue
            data = f"{CALLBACK_PREFIX}{action_id}"
        if len(data.encode("utf-8")) > 64:
            LOGGER.warning("Callback data too long for action_id=%s data=%s", action.id, data)
            continue
        row.append(InlineKeyboardButton(action.label, callback_data=data))
        if len(row) == columns or index == len(actions):
            buttons.append(row)
            row = []
    return InlineKeyboardMarkup(buttons) if buttons else None
