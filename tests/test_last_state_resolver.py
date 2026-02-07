from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app.bot import handlers
from app.core.last_state_resolver import resolve_short_message
from app.infra.last_state_store import LastState
from app.infra.request_context import RequestContext


def test_resolve_move_tomorrow() -> None:
    last_state = LastState(
        last_intent="utility_calendar.add",
        last_event_id="evt-1",
        last_reminder_id=None,
        last_calendar_id=None,
        last_query=None,
        last_correlation_id="cid-1",
        updated_at=datetime.now(timezone.utc),
    )
    resolution = resolve_short_message("перенеси на завтра", last_state)
    assert resolution.status == "matched"
    assert resolution.action == "move_tomorrow"
    assert resolution.target_id == "evt-1"


def test_resolve_move_phrase() -> None:
    last_state = LastState(
        last_intent="utility_calendar.add",
        last_event_id="evt-42",
        last_reminder_id=None,
        last_calendar_id=None,
        last_query=None,
        last_correlation_id="cid-3",
        updated_at=datetime.now(timezone.utc),
    )
    resolution = resolve_short_message("сдвинь на 30 минут", last_state)
    assert resolution.status == "matched"
    assert resolution.action == "move"
    assert resolution.target_id == "evt-42"


def test_resolve_cancel_reminder() -> None:
    last_state = LastState(
        last_intent="utility_reminders.create",
        last_event_id=None,
        last_reminder_id="rem-1",
        last_calendar_id=None,
        last_query=None,
        last_correlation_id="cid-2",
        updated_at=datetime.now(timezone.utc),
    )
    resolution = resolve_short_message("отмени это", last_state)
    assert resolution.status == "matched"
    assert resolution.action == "cancel"
    assert resolution.target == "reminder"
    assert resolution.target_id == "rem-1"


def test_resolve_fallback_without_last_state() -> None:
    resolution = resolve_short_message("перенеси на завтра", None)
    assert resolution.status == "fallback"
    assert resolution.action == "move_tomorrow"


def test_resolution_logging_includes_correlation_id(caplog) -> None:
    logger = logging.getLogger("app.bot.handlers")
    caplog.set_level(logging.INFO, logger=logger.name)
    request_context = RequestContext(
        correlation_id="cid-logging",
        user_id=1,
        chat_id=2,
        message_id=3,
        timezone=None,
        ts=datetime.now(timezone.utc),
        env="prod",
    )

    handlers._log_memory_resolution(request_context, used=True, reason="matched", matched_ref="last_event_id")

    payload = json.loads(caplog.records[-1].message)
    assert payload["event"] == "memory.resolution"
    assert payload["correlation_id"] == "cid-logging"
