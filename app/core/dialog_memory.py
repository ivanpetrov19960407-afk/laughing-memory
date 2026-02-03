from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

LOGGER = logging.getLogger(__name__)


DialogRole = Literal["user", "assistant"]


@dataclass(frozen=True)
class DialogMessage:
    role: DialogRole
    text: str
    ts: str


class DialogMemory:
    def __init__(
        self,
        path: Path,
        *,
        max_turns: int = 5,
        max_text_length: int = 2000,
    ) -> None:
        self._path = path
        self._max_turns = max(1, max_turns)
        self._max_messages = self._max_turns * 2
        self._max_text_length = max(1, max_text_length)
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] = {"users": {}}

    async def load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Dialog memory corrupted at %s: %s. Starting fresh.", self._path, exc)
            self._data = {"users": {}}
            return
        if not isinstance(data, dict):
            LOGGER.warning("Dialog memory invalid at %s. Starting fresh.", self._path)
            self._data = {"users": {}}
            return
        self._data = data
        self._trim_all()

    async def add_user(self, user_id: int, chat_id: int, text: str) -> None:
        await self._add_message(user_id, chat_id, "user", text)

    async def add_assistant(self, user_id: int, chat_id: int, text: str) -> None:
        await self._add_message(user_id, chat_id, "assistant", text)

    async def get_context(self, user_id: int, chat_id: int) -> list[DialogMessage]:
        async with self._lock:
            messages = self._get_messages(user_id, chat_id)
            return [DialogMessage(**message) for message in messages]

    async def clear(self, user_id: int, chat_id: int) -> None:
        async with self._lock:
            user = self._get_user(user_id)
            chats = user.setdefault("chats", {})
            chats[str(chat_id)] = []
            await self._persist_locked()

    async def set_enabled(self, user_id: int, enabled: bool) -> None:
        async with self._lock:
            user = self._get_user(user_id)
            user["enabled"] = bool(enabled)
            await self._persist_locked()

    async def is_enabled(self, user_id: int) -> bool:
        async with self._lock:
            user = self._get_user(user_id, create=False)
            if not user:
                return True
            return bool(user.get("enabled", True))

    async def get_status(self, user_id: int, chat_id: int) -> tuple[bool, int]:
        async with self._lock:
            user = self._get_user(user_id, create=False)
            enabled = True if not user else bool(user.get("enabled", True))
            messages = self._get_messages(user_id, chat_id)
            return enabled, len(messages)

    def format_context(self, messages: list[DialogMessage]) -> str:
        lines = [f"[{message.role}] {message.text}" for message in messages]
        return "\n".join(lines)

    async def _add_message(self, user_id: int, chat_id: int, role: DialogRole, text: str) -> None:
        trimmed = self._sanitize_text(text)
        if not trimmed:
            return
        async with self._lock:
            user = self._get_user(user_id)
            chats = user.setdefault("chats", {})
            chat_key = str(chat_id)
            messages = chats.setdefault(chat_key, [])
            messages.append(
                {
                    "role": role,
                    "text": trimmed,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
            if len(messages) > self._max_messages:
                chats[chat_key] = messages[-self._max_messages :]
            await self._persist_locked()

    def _get_user(self, user_id: int, *, create: bool = True) -> dict[str, Any]:
        users = self._data.setdefault("users", {})
        key = str(user_id)
        if create:
            return users.setdefault(key, {"enabled": True, "chats": {}})
        return users.get(key, {})

    def _get_messages(self, user_id: int, chat_id: int) -> list[dict[str, Any]]:
        user = self._get_user(user_id)
        chats = user.setdefault("chats", {})
        return chats.setdefault(str(chat_id), [])

    def _sanitize_text(self, text: str) -> str:
        trimmed = (text or "").strip()
        if not trimmed:
            return ""
        if len(trimmed) > self._max_text_length:
            trimmed = trimmed[: self._max_text_length].rstrip()
        return trimmed

    async def _persist_locked(self) -> None:
        self._trim_all()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"users": self._data.get("users", {})}
        tmp_path = self._path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        tmp_path.replace(self._path)

    def _trim_all(self) -> None:
        users = self._data.get("users", {})
        for user in users.values():
            chats = user.get("chats", {})
            for chat_id, messages in list(chats.items()):
                if not isinstance(messages, list):
                    chats[chat_id] = []
                    continue
                if len(messages) > self._max_messages:
                    chats[chat_id] = messages[-self._max_messages :]
