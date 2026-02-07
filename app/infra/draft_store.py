from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.core.calendar_nlp_ru import EventDraft


@dataclass
class DraftEntry:
    draft: EventDraft
    created_at: datetime
    updated_at: datetime


class DraftStore:
    def __init__(
        self,
        *,
        max_items: int = 50,
        ttl_seconds: int = 24 * 3600,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._max_items = max(1, max_items)
        self._ttl = timedelta(seconds=max(1, ttl_seconds))
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._store: dict[tuple[int, int], dict[str, DraftEntry]] = {}
        self._order: dict[tuple[int, int], deque[str]] = {}
        self._active: dict[tuple[int, int], str] = {}
        self._force_nlp: set[tuple[int, int]] = set()

    def save_draft(self, *, chat_id: int, user_id: int, draft_id: str, draft: EventDraft) -> None:
        key = (chat_id, user_id)
        now = self._now_provider()
        entries = self._store.setdefault(key, {})
        entries[draft_id] = DraftEntry(draft=draft, created_at=now, updated_at=now)
        order = self._order.setdefault(key, deque())
        order.append(draft_id)
        self._cleanup(key)
        self._enforce_limit(key)

    def get_draft(self, *, chat_id: int, user_id: int, draft_id: str) -> EventDraft | None:
        key = (chat_id, user_id)
        self._cleanup(key)
        entry = self._store.get(key, {}).get(draft_id)
        if entry is None:
            return None
        return entry.draft

    def update_draft(self, *, chat_id: int, user_id: int, draft_id: str, draft: EventDraft) -> None:
        key = (chat_id, user_id)
        entry = self._store.get(key, {}).get(draft_id)
        if entry is None:
            return
        now = self._now_provider()
        self._store[key][draft_id] = DraftEntry(draft=draft, created_at=entry.created_at, updated_at=now)

    def delete_draft(self, *, chat_id: int, user_id: int, draft_id: str) -> None:
        key = (chat_id, user_id)
        if key in self._store:
            self._store[key].pop(draft_id, None)
        if key in self._order:
            self._order[key] = deque([value for value in self._order[key] if value != draft_id])
        if self._active.get(key) == draft_id:
            self._active.pop(key, None)

    def set_active_draft(self, *, chat_id: int, user_id: int, draft_id: str | None) -> None:
        key = (chat_id, user_id)
        if draft_id is None:
            self._active.pop(key, None)
        else:
            self._active[key] = draft_id

    def get_active_draft_id(self, *, chat_id: int, user_id: int) -> str | None:
        key = (chat_id, user_id)
        self._cleanup(key)
        return self._active.get(key)

    def set_force_nlp(self, *, chat_id: int, user_id: int, enabled: bool) -> None:
        key = (chat_id, user_id)
        if enabled:
            self._force_nlp.add(key)
        else:
            self._force_nlp.discard(key)

    def consume_force_nlp(self, *, chat_id: int, user_id: int) -> bool:
        key = (chat_id, user_id)
        if key in self._force_nlp:
            self._force_nlp.discard(key)
            return True
        return False

    def _cleanup(self, key: tuple[int, int]) -> None:
        entries = self._store.get(key)
        if not entries:
            return
        now = self._now_provider()
        expired = [
            draft_id
            for draft_id, entry in entries.items()
            if now - entry.updated_at > self._ttl
        ]
        for draft_id in expired:
            entries.pop(draft_id, None)
        if not entries:
            self._store.pop(key, None)
            self._order.pop(key, None)
            self._active.pop(key, None)

    def _enforce_limit(self, key: tuple[int, int]) -> None:
        entries = self._store.get(key)
        order = self._order.get(key)
        if not entries or not order:
            return
        while len(entries) > self._max_items and order:
            draft_id = order.popleft()
            entries.pop(draft_id, None)
