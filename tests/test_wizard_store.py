from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.storage.wizard_store import WizardState, WizardStore


def test_wizard_store_lifecycle(tmp_path) -> None:
    store = WizardStore(tmp_path, timeout_seconds=300)
    now = datetime(2026, 2, 5, 12, 0, tzinfo=timezone.utc)
    state = WizardState(
        wizard_id="calendar.add_event",
        step="await_datetime",
        data={"draft": True},
        started_at=now,
        updated_at=now,
    )
    store.save_state(user_id=1, chat_id=10, state=state)

    loaded, expired = store.load_state(user_id=1, chat_id=10, now=now)
    assert expired is False
    assert loaded is not None
    assert loaded.wizard_id == "calendar.add_event"

    updated = replace(state, step="confirm", data={"title": "Врач"}, updated_at=now + timedelta(minutes=1))
    store.save_state(user_id=1, chat_id=10, state=updated)

    loaded, expired = store.load_state(user_id=1, chat_id=10, now=now + timedelta(minutes=1))
    assert expired is False
    assert loaded is not None
    assert loaded.step == "confirm"
    assert loaded.data["title"] == "Врач"

    store.clear_state(user_id=1, chat_id=10)
    loaded, expired = store.load_state(user_id=1, chat_id=10, now=now + timedelta(minutes=2))
    assert loaded is None
    assert expired is False


def test_wizard_store_timeout(tmp_path) -> None:
    store = WizardStore(tmp_path, timeout_seconds=60)
    now = datetime(2026, 2, 5, 12, 0, tzinfo=timezone.utc)
    state = WizardState(
        wizard_id="calendar.add_event",
        step="await_title",
        data={"dt": "2026-02-05 18:30"},
        started_at=now - timedelta(minutes=5),
        updated_at=now - timedelta(minutes=2),
    )
    store.save_state(user_id=2, chat_id=20, state=state)

    loaded, expired = store.load_state(user_id=2, chat_id=20, now=now)
    assert loaded is None
    assert expired is True
