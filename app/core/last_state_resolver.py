from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.infra.last_state_store import LastState
from app.core.recurrence_scope import RecurrenceScope, parse_recurrence_scope


ResolutionStatus = Literal["matched", "fallback", "skip"]


@dataclass(frozen=True)
class ResolutionResult:
    status: ResolutionStatus
    reason: str
    action: str | None = None
    target: str | None = None
    target_id: str | None = None
    query: str | None = None
    matched_ref: str | None = None
    scope: RecurrenceScope | None = None


_TRIGGER_TOKENS = {
    "это",
    "его",
    "её",
    "ее",
    "туда",
    "завтра",
    "отмени",
    "отменить",
    "перенеси",
    "перенести",
    "повтори",
    "повторить",
}
_REPEAT_PHRASES = (
    "как в прошлый раз",
    "сделай как в прошлый раз",
)


def resolve_short_message(text: str, last_state: LastState | None) -> ResolutionResult:
    cleaned = text.strip()
    if not cleaned:
        return ResolutionResult(status="skip", reason="empty")
    lowered = cleaned.lower()
    scope = parse_recurrence_scope(lowered)
    tokens = re.findall(r"\w+", lowered)
    is_short = len(tokens) <= 5
    has_trigger = any(token in _TRIGGER_TOKENS for token in tokens) or any(
        phrase in lowered for phrase in _REPEAT_PHRASES
    )
    if not is_short and not has_trigger:
        return ResolutionResult(status="skip", reason="not_short")
    action = _infer_action(lowered)
    if action is None:
        return ResolutionResult(status="skip", reason="no_action_match")
    if last_state is None:
        return ResolutionResult(status="fallback", reason="missing_last_state", action=action, scope=scope)
    if action == "repeat_search":
        query = last_state.last_query
        if isinstance(query, str) and query.strip():
            return ResolutionResult(
                status="matched",
                reason="matched_last_query",
                action=action,
                target="search",
                query=query,
                matched_ref="last_query",
                scope=scope,
            )
        return ResolutionResult(status="fallback", reason="missing_last_query", action=action, scope=scope)
    if action == "move":
        return ResolutionResult(status="fallback", reason="missing_date", action=action, scope=scope)
    if action == "move_tomorrow":
        event_id = last_state.last_event_id
        if isinstance(event_id, str) and event_id.strip():
            return ResolutionResult(
                status="matched",
                reason="matched_last_event",
                action=action,
                target="event",
                target_id=event_id,
                matched_ref="last_event_id",
                scope=scope,
            )
        return ResolutionResult(status="fallback", reason="missing_last_event", action=action, scope=scope)
    if action == "cancel":
        reminder_id = last_state.last_reminder_id
        if isinstance(reminder_id, str) and reminder_id.strip():
            return ResolutionResult(
                status="matched",
                reason="matched_last_reminder",
                action=action,
                target="reminder",
                target_id=reminder_id,
                matched_ref="last_reminder_id",
                scope=scope,
            )
        event_id = last_state.last_event_id
        if isinstance(event_id, str) and event_id.strip():
            return ResolutionResult(
                status="matched",
                reason="matched_last_event",
                action=action,
                target="event",
                target_id=event_id,
                matched_ref="last_event_id",
                scope=scope,
            )
        return ResolutionResult(status="fallback", reason="missing_last_target", action=action, scope=scope)
    return ResolutionResult(status="skip", reason="unsupported_action")


def _infer_action(lowered: str) -> str | None:
    if "перенеси" in lowered or "перенести" in lowered:
        if "завтра" in lowered:
            return "move_tomorrow"
        return "move"
    if "отмени" in lowered or "отменить" in lowered:
        return "cancel"
    if any(phrase in lowered for phrase in _REPEAT_PHRASES) or "повтори" in lowered or "повторить" in lowered:
        return "repeat_search"
    return None
