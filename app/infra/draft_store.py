from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable


@dataclass
class DraftEntry:
    draft_id: str
    chat_id: int
    user_id: int
    draft: object
    status: str
    created_at: datetime
    updated_at: datetime
    expect_reparse: bool = False


class DraftStore:
    def __init__(
        self,
        *,
        max_items: int = 200,
        ttl_seconds: int = 24 * 3600,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._max_items = max(1, max_items)
        self._ttl = timedelta(seconds=max(60, ttl_seconds))
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._entries: dict[str, DraftEntry] = {}
        self._order: list[str] = []

    def create(self, *, chat_id: int, user_id: int, draft: object, status: str) -> DraftEntry:
        draft_id = str(uuid.uuid4())
        now = self._now()
        entry = DraftEntry(
            draft_id=draft_id,
            chat_id=chat_id,
            user_id=user_id,
            draft=draft,
            status=status,
            created_at=now,
            updated_at=now,
        )
        self._entries[draft_id] = entry
        self._order.append(draft_id)
        self._cleanup()
        return entry

    def get(self, *, draft_id: str, chat_id: int, user_id: int) -> DraftEntry | None:
        self._cleanup()
        entry = self._entries.get(draft_id)
        if entry is None:
            return None
        if entry.chat_id != chat_id or entry.user_id != user_id:
            return None
        return entry

    def find_active(self, *, chat_id: int, user_id: int) -> DraftEntry | None:
        self._cleanup()
        for draft_id in reversed(self._order):
            entry = self._entries.get(draft_id)
            if entry is None:
                continue
            if entry.chat_id == chat_id and entry.user_id == user_id and entry.status in {"awaiting", "draft"}:
                return entry
        return None

    def update(self, entry: DraftEntry) -> None:
        entry.updated_at = self._now()
        self._entries[entry.draft_id] = entry
        self._cleanup()

    def remove(self, *, draft_id: str) -> None:
        self._entries.pop(draft_id, None)
        self._order = [item for item in self._order if item != draft_id]

    def _cleanup(self) -> None:
        now = self._now()
        expired = [
            draft_id
            for draft_id, entry in self._entries.items()
            if now - entry.updated_at > self._ttl
        ]
        for draft_id in expired:
            self.remove(draft_id=draft_id)
        if len(self._order) > self._max_items:
            overflow = len(self._order) - self._max_items
            for draft_id in self._order[:overflow]:
                self._entries.pop(draft_id, None)
            self._order = self._order[overflow:]
