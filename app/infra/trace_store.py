from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.infra.request_context import RequestContext


@dataclass(frozen=True)
class TraceEntry:
    correlation_id: str
    ts: datetime
    intent: str | None
    mode: str | None
    status: str
    total_duration_ms: float | None
    trace_steps: list[dict[str, Any]]
    durations: dict[str, float]
    tool_names: list[str]
    llm_models: list[str]
    tool_calls: list[dict[str, Any]]
    llm_calls: list[dict[str, Any]]
    error: dict[str, str] | None


class TraceStore:
    def __init__(
        self,
        *,
        max_items: int = 20,
        ttl_seconds: int = 86400,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._max_items = max(1, max_items)
        self._ttl = timedelta(seconds=max(1, ttl_seconds))
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._store: dict[tuple[int, int], deque[TraceEntry]] = {}

    def add_entry(self, *, chat_id: int, user_id: int, entry: TraceEntry) -> None:
        key = (chat_id, user_id)
        entries = self._store.setdefault(key, deque(maxlen=self._max_items))
        entries.append(entry)
        self._cleanup(key)

    def add_from_context(
        self,
        *,
        chat_id: int,
        user_id: int,
        request_context: RequestContext,
        total_duration_ms: float | None,
    ) -> None:
        entry = build_trace_entry(request_context, total_duration_ms=total_duration_ms)
        self.add_entry(chat_id=chat_id, user_id=user_id, entry=entry)

    def list_entries(self, *, chat_id: int, user_id: int, limit: int = 5) -> list[TraceEntry]:
        key = (chat_id, user_id)
        self._cleanup(key)
        entries = self._store.get(key)
        if not entries:
            return []
        return list(reversed(entries))[: max(1, limit)]

    def get_last_entry(self, *, chat_id: int, user_id: int) -> TraceEntry | None:
        key = (chat_id, user_id)
        self._cleanup(key)
        entries = self._store.get(key)
        if not entries:
            return None
        return entries[-1]

    def count_entries(self) -> int:
        total = 0
        for key in list(self._store.keys()):
            self._cleanup(key)
            entries = self._store.get(key)
            if entries:
                total += len(entries)
        return total

    def find_entries(self, *, chat_id: int, user_id: int, correlation_id: str) -> list[TraceEntry]:
        key = (chat_id, user_id)
        self._cleanup(key)
        entries = self._store.get(key)
        if not entries:
            return []
        target = correlation_id.strip().lower()
        matches = [entry for entry in entries if entry.correlation_id.lower().startswith(target)]
        if matches:
            return matches
        return [entry for entry in entries if entry.correlation_id.lower() == target]

    def _cleanup(self, key: tuple[int, int]) -> None:
        entries = self._store.get(key)
        if not entries:
            return
        now = self._now_provider()
        fresh_entries = deque(
            [entry for entry in entries if now - entry.ts <= self._ttl],
            maxlen=self._max_items,
        )
        if fresh_entries:
            self._store[key] = fresh_entries
        else:
            self._store.pop(key, None)


def build_trace_entry(request_context: RequestContext, *, total_duration_ms: float | None) -> TraceEntry:
    trace_steps = [
        {
            "step": step.get("step"),
            "component": step.get("component"),
            "name": step.get("name"),
            "status": step.get("status"),
            "duration_ms": step.get("duration_ms"),
        }
        for step in request_context.trace
    ]
    tool_calls = [
        {
            "name": step.get("name"),
            "status": step.get("status"),
            "duration_ms": step.get("duration_ms"),
        }
        for step in trace_steps
        if step.get("component") == "tool" and step.get("status") != "start"
    ]
    llm_calls = [
        {
            "name": step.get("name"),
            "status": step.get("status"),
            "duration_ms": step.get("duration_ms"),
        }
        for step in trace_steps
        if step.get("component") == "llm" and step.get("status") != "start"
    ]
    tool_names = sorted({call["name"] for call in tool_calls if call.get("name")})
    llm_models = sorted({call["name"] for call in llm_calls if call.get("name")})
    meta = request_context.meta
    intent = meta.get("intent") if isinstance(meta, dict) else None
    mode = meta.get("mode") if isinstance(meta, dict) else None
    error_payload = meta.get("error") if isinstance(meta, dict) else None
    llm_error = meta.get("llm_error") if isinstance(meta, dict) else None
    error = None
    if isinstance(error_payload, dict):
        exc_type = error_payload.get("exc_type")
        where = error_payload.get("where")
        if isinstance(exc_type, str) and isinstance(where, str):
            error = {"exc_type": exc_type, "where": where}
    if error is None and isinstance(llm_error, dict):
        exc_type = llm_error.get("error_type")
        if isinstance(exc_type, str):
            error = {"exc_type": exc_type, "where": "llm"}
    durations: dict[str, float] = {}
    if total_duration_ms is not None:
        durations["total_ms"] = round(total_duration_ms, 2)
    if not isinstance(intent, str):
        for step in reversed(trace_steps):
            if step.get("step") == "orchestrator.end" and isinstance(step.get("name"), str):
                intent = step.get("name")
                break
    return TraceEntry(
        correlation_id=request_context.correlation_id,
        ts=request_context.ts,
        intent=intent if isinstance(intent, str) else None,
        mode=mode if isinstance(mode, str) else None,
        status=request_context.status,
        total_duration_ms=round(total_duration_ms, 2) if total_duration_ms is not None else None,
        trace_steps=trace_steps,
        durations=durations,
        tool_names=tool_names,
        llm_models=llm_models,
        tool_calls=tool_calls,
        llm_calls=llm_calls,
        error=error,
    )
