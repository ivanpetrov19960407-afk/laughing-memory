"""Tests for wizard calendar.add with natural language input (Stage 4)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from app.bot.wizard import WizardManager, WIZARD_CALENDAR_ADD
from app.storage.wizard_store import WizardStore, WizardState
from app.core import calendar_store, tools_calendar_caldav


def _setup_env(tmp_path, monkeypatch):
    """Common env setup for wizard tests."""
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "pass")

    async def fake_create_event(*args, **kwargs) -> tools_calendar_caldav.CreatedEvent:
        return tools_calendar_caldav.CreatedEvent(uid="evt-1", href="https://caldav.example.com/e/1")

    monkeypatch.setattr("app.core.tools_calendar_caldav.create_event", fake_create_event)


def test_wizard_natural_input_tomorrow_time_title(tmp_path, monkeypatch) -> None:
    """User sends 'завтра 19:00 врач' - wizard should skip title step and go to confirm."""
    _setup_env(tmp_path, monkeypatch)
    store = WizardStore(tmp_path / "wizards", timeout_seconds=600)
    manager = WizardManager(store)

    # Start wizard
    start = asyncio.run(
        manager.handle_action(
            user_id=1,
            chat_id=10,
            op="wizard_start",
            payload={"wizard_id": WIZARD_CALENDAR_ADD},
        )
    )
    assert start is not None
    # Prompt should contain examples of free-form input
    assert "завтра" in start.text.lower() or "YYYY-MM-DD" in start.text

    # Send natural language input with date+time+title
    step = asyncio.run(manager.handle_text(user_id=1, chat_id=10, text="завтра 19:00 врач"))
    assert step is not None
    # Should jump to confirm (not ask for title)
    assert "врач" in step.text.lower()
    assert "создать" in step.text.lower() or "верно" in step.text.lower()

    # Before confirm, check that no event is created yet
    items_before = asyncio.run(calendar_store.list_items(None, None))
    assert not any(item.title == "врач" for item in items_before)

    # Confirm
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
    assert "Событие создано" in confirm.text

    # Event should be created
    items_after = asyncio.run(calendar_store.list_items(None, None))
    assert any(item.title == "врач" for item in items_after)

    # Wizard should be cleared
    state, _expired = store.load_state(user_id=1, chat_id=10)
    assert state is None


def test_wizard_natural_input_today_time_title(tmp_path, monkeypatch) -> None:
    """User sends 'сегодня 18:30 созвон'."""
    _setup_env(tmp_path, monkeypatch)
    store = WizardStore(tmp_path / "wizards", timeout_seconds=600)
    manager = WizardManager(store)

    asyncio.run(
        manager.handle_action(
            user_id=2,
            chat_id=20,
            op="wizard_start",
            payload={"wizard_id": WIZARD_CALENDAR_ADD},
        )
    )
    step = asyncio.run(manager.handle_text(user_id=2, chat_id=20, text="сегодня 18:30 созвон"))
    assert step is not None
    assert "созвон" in step.text.lower()

    confirm = asyncio.run(
        manager.handle_action(user_id=2, chat_id=20, op="wizard_confirm", payload={})
    )
    assert confirm.status == "ok"
    assert "Событие создано" in confirm.text


def test_wizard_natural_input_relative_time(tmp_path, monkeypatch) -> None:
    """User sends 'через 2 часа тренировка'."""
    _setup_env(tmp_path, monkeypatch)
    store = WizardStore(tmp_path / "wizards", timeout_seconds=600)
    manager = WizardManager(store)

    asyncio.run(
        manager.handle_action(
            user_id=3,
            chat_id=30,
            op="wizard_start",
            payload={"wizard_id": WIZARD_CALENDAR_ADD},
        )
    )
    step = asyncio.run(manager.handle_text(user_id=3, chat_id=30, text="через 2 часа тренировка"))
    assert step is not None
    assert "тренировка" in step.text.lower()

    confirm = asyncio.run(
        manager.handle_action(user_id=3, chat_id=30, op="wizard_confirm", payload={})
    )
    assert confirm.status == "ok"
    assert "Событие создано" in confirm.text


def test_wizard_natural_input_weekday(tmp_path, monkeypatch) -> None:
    """User sends 'в пятницу 10:15 встреча'."""
    _setup_env(tmp_path, monkeypatch)
    store = WizardStore(tmp_path / "wizards", timeout_seconds=600)
    manager = WizardManager(store)

    asyncio.run(
        manager.handle_action(
            user_id=4,
            chat_id=40,
            op="wizard_start",
            payload={"wizard_id": WIZARD_CALENDAR_ADD},
        )
    )
    step = asyncio.run(manager.handle_text(user_id=4, chat_id=40, text="в пятницу 10:15 встреча"))
    assert step is not None
    assert "встреча" in step.text.lower()

    confirm = asyncio.run(
        manager.handle_action(user_id=4, chat_id=40, op="wizard_confirm", payload={})
    )
    assert confirm.status == "ok"
    assert "Событие создано" in confirm.text


def test_wizard_natural_input_day_month(tmp_path, monkeypatch) -> None:
    """User sends '07.02 12:00 стоматолог'."""
    _setup_env(tmp_path, monkeypatch)
    store = WizardStore(tmp_path / "wizards", timeout_seconds=600)
    manager = WizardManager(store)

    asyncio.run(
        manager.handle_action(
            user_id=5,
            chat_id=50,
            op="wizard_start",
            payload={"wizard_id": WIZARD_CALENDAR_ADD},
        )
    )
    step = asyncio.run(manager.handle_text(user_id=5, chat_id=50, text="07.02 12:00 стоматолог"))
    assert step is not None
    assert "стоматолог" in step.text.lower()

    confirm = asyncio.run(
        manager.handle_action(user_id=5, chat_id=50, op="wizard_confirm", payload={})
    )
    assert confirm.status == "ok"
    assert "Событие создано" in confirm.text


def test_wizard_natural_input_time_only_asks_title(tmp_path, monkeypatch) -> None:
    """User sends only date/time without title - wizard should ask for title."""
    _setup_env(tmp_path, monkeypatch)
    store = WizardStore(tmp_path / "wizards", timeout_seconds=600)
    manager = WizardManager(store)

    asyncio.run(
        manager.handle_action(
            user_id=6,
            chat_id=60,
            op="wizard_start",
            payload={"wizard_id": WIZARD_CALENDAR_ADD},
        )
    )
    # Send only datetime, no title
    step = asyncio.run(manager.handle_text(user_id=6, chat_id=60, text="завтра 19:00"))
    assert step is not None
    # Should ask for title
    assert "назван" in step.text.lower() or "название" in step.text.lower()

    # Now send title
    step2 = asyncio.run(manager.handle_text(user_id=6, chat_id=60, text="Врач"))
    assert step2 is not None
    assert "создать" in step2.text.lower() or "верно" in step2.text.lower()

    confirm = asyncio.run(
        manager.handle_action(user_id=6, chat_id=60, op="wizard_confirm", payload={})
    )
    assert confirm.status == "ok"


def test_wizard_old_flow_still_works(tmp_path, monkeypatch) -> None:
    """Regression: old flow (strict datetime then title) still works."""
    _setup_env(tmp_path, monkeypatch)
    store = WizardStore(tmp_path / "wizards", timeout_seconds=600)
    manager = WizardManager(store)

    # Start
    asyncio.run(
        manager.handle_action(
            user_id=7,
            chat_id=70,
            op="wizard_start",
            payload={"wizard_id": WIZARD_CALENDAR_ADD},
        )
    )

    # Send strict datetime
    step_one = asyncio.run(manager.handle_text(user_id=7, chat_id=70, text="2026-02-05 18:30"))
    assert step_one is not None
    assert "назван" in step_one.text.lower() or "название" in step_one.text.lower()

    # Send title
    step_two = asyncio.run(manager.handle_text(user_id=7, chat_id=70, text="Врач"))
    assert step_two is not None
    assert "создать" in step_two.text.lower() or "верно" in step_two.text.lower()

    # Confirm
    confirm = asyncio.run(
        manager.handle_action(user_id=7, chat_id=70, op="wizard_confirm", payload={})
    )
    assert confirm.status == "ok"
    assert "Событие создано" in confirm.text

    # Verify event created
    items = asyncio.run(calendar_store.list_items(None, None))
    assert any(item.title == "Врач" for item in items)


def test_wizard_invalid_input_shows_error(tmp_path, monkeypatch) -> None:
    """Invalid input should show error with examples."""
    _setup_env(tmp_path, monkeypatch)
    store = WizardStore(tmp_path / "wizards", timeout_seconds=600)
    manager = WizardManager(store)

    asyncio.run(
        manager.handle_action(
            user_id=8,
            chat_id=80,
            op="wizard_start",
            payload={"wizard_id": WIZARD_CALENDAR_ADD},
        )
    )
    invalid = asyncio.run(manager.handle_text(user_id=8, chat_id=80, text="абракадабра"))
    assert invalid is not None
    assert invalid.status == "refused"


def test_wizard_event_not_created_before_confirm(tmp_path, monkeypatch) -> None:
    """Event must NOT be created until wizard_confirm is received."""
    _setup_env(tmp_path, monkeypatch)
    store = WizardStore(tmp_path / "wizards", timeout_seconds=600)
    manager = WizardManager(store)

    asyncio.run(
        manager.handle_action(
            user_id=9,
            chat_id=90,
            op="wizard_start",
            payload={"wizard_id": WIZARD_CALENDAR_ADD},
        )
    )
    asyncio.run(manager.handle_text(user_id=9, chat_id=90, text="завтра 19:00 врач"))

    # No event should exist before confirm
    items = asyncio.run(calendar_store.list_items(None, None))
    assert not any(item.title == "врач" for item in items)

    # Now confirm
    confirm = asyncio.run(
        manager.handle_action(user_id=9, chat_id=90, op="wizard_confirm", payload={})
    )
    assert confirm.status == "ok"

    # Now event exists
    items_after = asyncio.run(calendar_store.list_items(None, None))
    assert any(item.title == "врач" for item in items_after)
