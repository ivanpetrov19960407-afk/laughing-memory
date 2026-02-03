from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.bot.wizard import WizardManager, WIZARD_CALENDAR_ADD
from app.storage.wizard_store import WizardStore, WizardState
from app.core import calendar_store


def test_wizard_add_event_flow(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    store = WizardStore(tmp_path / "wizards", timeout_seconds=600)
    manager = WizardManager(store)

    start = asyncio.run(
        manager.handle_action(
            user_id=1,
            chat_id=10,
            op="wizard_start",
            payload={"wizard_id": WIZARD_CALENDAR_ADD},
        )
    )
    assert start is not None
    assert "YYYY-MM-DD" in start.text

    invalid = asyncio.run(manager.handle_text(user_id=1, chat_id=10, text="wrong"))
    assert invalid is not None
    assert invalid.status == "refused"

    step_one = asyncio.run(manager.handle_text(user_id=1, chat_id=10, text="2026-02-05 18:30"))
    assert step_one is not None
    assert "название" in step_one.text.lower()

    step_two = asyncio.run(manager.handle_text(user_id=1, chat_id=10, text="Врач"))
    assert step_two is not None
    assert "создать" in step_two.text.lower()

    confirm = asyncio.run(
        manager.handle_action(
            user_id=1,
            chat_id=10,
            op="wizard_confirm",
            payload={},
        )
    )
    assert confirm is not None
    assert confirm.status == "ok"
    assert "Событие добавлено" in confirm.text

    state, _expired = store.load_state(user_id=1, chat_id=10)
    assert state is None

    items = asyncio.run(calendar_store.list_items(None, None))
    assert any(item.title == "Врач" for item in items)


def test_wizard_cancel_and_timeout(tmp_path) -> None:
    store = WizardStore(tmp_path / "wizards", timeout_seconds=60)
    manager = WizardManager(store)
    now = datetime.now(timezone.utc)
    state = WizardState(
        wizard_id=WIZARD_CALENDAR_ADD,
        step="await_datetime",
        data={},
        started_at=now,
        updated_at=now,
    )
    store.save_state(user_id=5, chat_id=50, state=state)

    cancelled = manager.cancel(user_id=5, chat_id=50)
    assert cancelled.status == "refused"

    expired_state = WizardState(
        wizard_id=WIZARD_CALENDAR_ADD,
        step="await_title",
        data={},
        started_at=now - timedelta(minutes=5),
        updated_at=now - timedelta(minutes=3),
    )
    store.save_state(user_id=6, chat_id=60, state=expired_state)

    result = asyncio.run(manager.handle_text(user_id=6, chat_id=60, text="2026-02-05 18:30"))
    assert result is not None
    assert "истёк" in result.text.lower()
