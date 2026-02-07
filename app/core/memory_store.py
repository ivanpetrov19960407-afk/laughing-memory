from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

from app.infra.request_context import RequestContext, log_event

LOGGER = logging.getLogger(__name__)

MemoryRole = Literal["user", "assistant", "tool", "system"]
MemoryKind = Literal["message", "result", "fact", "intent"]

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{20,}\b")


@dataclass(frozen=True)
class MemoryItem:
    ts: datetime
    role: MemoryRole
    kind: MemoryKind
    content: str
    intent: str | None = None
    correlation_id: str | None = None
    status: str | None = None


class MemorySanitizer:
    def __init__(self, *, prod_limit: int = 500, dev_limit: int = 2000) -> None:
        self._prod_limit = max(1, prod_limit)
        self._dev_limit = max(1, dev_limit)

    def sanitize(self, text: str, *, env: str = "prod") -> str:
        trimmed = (text or "").strip()
        if not trimmed:
            return ""
        cleaned = _EMAIL_RE.sub("[email]", trimmed)
        cleaned = _PHONE_RE.sub("[phone]", cleaned)
        cleaned = _TOKEN_RE.sub("[secret]", cleaned)
        limit = self._dev_limit if env == "dev" else self._prod_limit
        if len(cleaned) > limit:
            cleaned = cleaned[:limit].rstrip() + "…"
        return cleaned


class MemoryStore:
    def __init__(
        self,
        *,
        max_items: int = 30,
        ttl_seconds: int = 7 * 24 * 60 * 60,
        sanitizer: MemorySanitizer | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._max_items = max(1, max_items)
        self._ttl_seconds = max(1, ttl_seconds)
        self._sanitizer = sanitizer or MemorySanitizer()
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._data: dict[tuple[int, int], deque[MemoryItem]] = {}

    def add(
        self,
        *,
        chat_id: int,
        user_id: int,
        role: MemoryRole,
        kind: MemoryKind,
        content: str,
        intent: str | None = None,
        correlation_id: str | None = None,
        status: str | None = None,
        env: str = "prod",
        ts: datetime | None = None,
    ) -> None:
        key = self._key(chat_id, user_id)
        if key is None:
            return
        sanitized = self._sanitizer.sanitize(content, env=env)
        if not sanitized:
            return
        item = MemoryItem(
            ts=ts or self._now(),
            role=role,
            kind=kind,
            content=sanitized,
            intent=intent,
            correlation_id=correlation_id,
            status=status,
        )
        items = self._data.setdefault(key, deque(maxlen=self._max_items))
        items.append(item)
        self._cleanup(key, now=self._now())

    def get_recent(
        self,
        *,
        chat_id: int,
        user_id: int,
        limit: int = 10,
    ) -> list[MemoryItem]:
        key = self._key(chat_id, user_id)
        if key is None:
            return []
        self._cleanup(key, now=self._now())
        items = self._data.get(key, deque())
        if not items:
            return []
        limited = list(items)[-max(1, limit) :]
        return limited

    def clear(self, *, chat_id: int, user_id: int) -> None:
        key = self._key(chat_id, user_id)
        if key is None:
            return
        self._data.pop(key, None)

    def count_entries(self) -> int:
        total = 0
        for key in list(self._data.keys()):
            self._cleanup(key, now=self._now())
            items = self._data.get(key)
            if items:
                total += len(items)
        return total

    def _key(self, chat_id: int, user_id: int) -> tuple[int, int] | None:
        if not chat_id or not user_id:
            return None
        return (int(chat_id), int(user_id))

    def _cleanup(self, key: tuple[int, int], *, now: datetime) -> None:
        items = self._data.get(key)
        if not items:
            return
        cutoff = now - timedelta(seconds=self._ttl_seconds)
        while items and items[0].ts < cutoff:
            items.popleft()
        if not items:
            self._data.pop(key, None)


def build_llm_context(
    request_context: RequestContext | None,
    memory_store: MemoryStore | None,
    *,
    limit: int = 10,
    max_chars: int = 2000,
) -> str | None:
    if request_context is None or memory_store is None:
        return None
    items = memory_store.get_recent(
        chat_id=int(request_context.chat_id or 0),
        user_id=int(request_context.user_id or 0),
        limit=limit,
    )
    if not items:
        return None
    correlation_id = request_context.correlation_id
    filtered = [item for item in items if item.correlation_id != correlation_id]
    if not filtered:
        return None

    def _preview(text: str, limit: int = 160) -> str:
        cleaned = text.replace("\n", " ").strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[:limit].rstrip() + "…"

    def _render(items_to_render: list[MemoryItem]) -> str:
        context_lines: list[str] = []
        action_lines: list[str] = []
        for item in items_to_render:
            preview = _preview(item.content)
            if item.kind in {"result", "intent"} or item.intent:
                intent = item.intent or "-"
                status = item.status or "-"
                action_lines.append(f"• {intent} ({status}) — {preview}")
            else:
                context_lines.append(f"• [{item.role}] {preview}")
        blocks: list[str] = []
        if context_lines:
            blocks.append("Недавний контекст:\n" + "\n".join(context_lines))
        if action_lines:
            blocks.append("Последние действия:\n" + "\n".join(action_lines))
        return "\n\n".join(blocks)

    rendered = ""
    used_items: list[MemoryItem] = []
    for start in range(len(filtered)):
        candidate = filtered[start:]
        candidate_text = _render(candidate)
        if len(candidate_text) <= max_chars:
            rendered = candidate_text
            used_items = candidate
            break
    if not rendered:
        rendered = _render(filtered[-1:])
        used_items = filtered[-1:]
        if len(rendered) > max_chars:
            if max_chars <= 1:
                rendered = "…"
            else:
                rendered = rendered[: max_chars - 1].rstrip() + "…"

    log_event(
        LOGGER,
        request_context,
        component="memory",
        event="memory.used",
        status="ok",
        items_used=len(used_items),
        chars=len(rendered),
    )
    return rendered.strip() if rendered.strip() else None
