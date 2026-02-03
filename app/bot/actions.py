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


@dataclass
class StoredAction:
    user_id: int
    chat_id: int
    intent: str
    payload: dict[str, Any]
    created_at: float
    expires_at: float


class ActionStore:
    def __init__(
        self,
        *,
        ttl_seconds: int = 600,
        max_items: int = 2000,
        max_payload_bytes: int = 2048,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_items = max_items
        self._max_payload_bytes = max_payload_bytes
        self._items: dict[str, StoredAction] = {}

    def store_action(self, *, action: Action, user_id: int, chat_id: int) -> str:
        self._cleanup()
        payload = action.payload or {}
        self._validate_payload(payload)
        token = self._generate_token()
        now = time.monotonic()
        self._items[token] = StoredAction(
            user_id=user_id,
            chat_id=chat_id,
            intent=action.id,
            payload=payload,
            created_at=now,
            expires_at=now + self._ttl_seconds,
        )
        return token

    def pop_action(self, *, user_id: int, chat_id: int, token: str) -> StoredAction | None:
        self._cleanup()
        item = self._items.get(token)
        if item is None or item.user_id != user_id or item.chat_id != chat_id:
            return None
        if item.expires_at < time.monotonic():
            self._items.pop(token, None)
            return None
        self._items.pop(token, None)
        return item

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
            for token in list(self._items.keys())[: len(self._items) - self._max_items]:
                self._items.pop(token, None)


def parse_callback_token(data: str | None) -> str | None:
    if not data:
        return None
    if not data.startswith(CALLBACK_PREFIX):
        return None
    return data[len(CALLBACK_PREFIX) :]


def build_inline_keyboard(
    actions: list[Action],
    *,
    store: ActionStore,
    user_id: int,
    chat_id: int,
    columns: int = 2,
) -> InlineKeyboardMarkup | None:
    if not actions:
        return None
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for index, action in enumerate(actions, start=1):
        try:
            token = store.store_action(action=action, user_id=user_id, chat_id=chat_id)
        except ValueError:
            LOGGER.warning("Action payload too large: action_id=%s", action.id)
            continue
        data = f"{CALLBACK_PREFIX}{token}"
        if len(data.encode("utf-8")) > 64:
            LOGGER.warning("Callback data too long for action_id=%s token=%s", action.id, token)
            continue
        row.append(InlineKeyboardButton(action.label, callback_data=data))
        if len(row) == columns or index == len(actions):
            buttons.append(row)
            row = []
    return InlineKeyboardMarkup(buttons) if buttons else None
