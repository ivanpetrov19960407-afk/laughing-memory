from __future__ import annotations

from datetime import datetime

from app.bot.handlers import _build_recurrence_scope_actions
from app.core import calendar_store


def test_recurrence_scope_actions_payloads() -> None:
    instance_dt = datetime(2026, 4, 1, 9, 0, tzinfo=calendar_store.BOT_TZ)
    actions = _build_recurrence_scope_actions(
        "calendar.delete",
        event_id="evt-1",
        instance_dt=instance_dt,
    )
    labels = [action.label for action in actions]
    assert labels == ["Только это", "Всю серию", "Это и будущие"]
    payloads = [action.payload for action in actions]
    assert payloads[0]["scope"] == "THIS"
    assert payloads[1]["scope"] == "ALL"
    assert payloads[2]["scope"] == "FUTURE"
    assert payloads[0]["event_id"] == "evt-1"
    assert payloads[0]["instance_dt"] == instance_dt.isoformat()
