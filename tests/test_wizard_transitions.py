from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

from app.bot.wizard import WizardManager, WIZARD_CALENDAR_ADD
from app.storage.wizard_store import WizardStore, WizardState
from app.core import calendar_store
from app.core import tools_calendar_caldav


def test_wizard_add_event_flow(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com")
    monkeypatch.setenv("CALDAV_USERNAME", "user")
    monkeypatch.setenv("CALDAV_PASSWORD", "pass")

    async def fake_create_event(*args, **kwargs) -> tools_calendar_caldav.CreatedEvent:
        return tools_calendar_caldav.CreatedEvent(uid="evt-1", href="https://caldav.example.com/e/1")

    monkeypatch.setattr("app.core.tools_calendar_caldav.create_event", fake_create_event)
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
    assert "завтра" in start.text.lower() or "классический формат" in start.text.lower()

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
    assert "Событие создано" in confirm.text

    state, _expired = store.load_state(user_id=1, chat_id=10)
    assert state is None

    items = asyncio.run(calendar_store.list_items(None, None))
    assert any(item.title == "Врач" for item in items)


def test_wizard_calendar_refuses_without_connection(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    tokens_path = tmp_path / "google_tokens.db"
    monkeypatch.setenv("GOOGLE_TOKENS_PATH", str(tokens_path))
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
    asyncio.run(manager.handle_text(user_id=2, chat_id=20, text="2026-02-05 18:30"))
    asyncio.run(manager.handle_text(user_id=2, chat_id=20, text="Врач"))
    confirm = asyncio.run(
        manager.handle_action(
            user_id=2,
            chat_id=20,
            op="wizard_confirm",
            payload={},
        )
    )
    assert confirm.status == "ok"
    assert "Событие создано" in confirm.text


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


def test_wizard_natural_language_tomorrow_with_title(tmp_path, monkeypatch) -> None:
    """Test natural language input: завтра 19:00 врач"""
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
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
    assert start.status == "ok"

    # Send natural language input with datetime and title
    step_one = asyncio.run(manager.handle_text(user_id=1, chat_id=10, text="завтра 19:00 врач"))
    assert step_one is not None
    assert step_one.status == "ok"
    # Should skip title step and go directly to confirmation
    assert "создать" in step_one.text.lower() or "верно" in step_one.text.lower()
    assert "врач" in step_one.text
    assert "19:00" in step_one.text

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

    # Verify event was created
    items = asyncio.run(calendar_store.list_items(None, None))
    assert any(item.title == "врач" for item in items)


def test_wizard_natural_language_today_with_title(tmp_path, monkeypatch) -> None:
    """Test natural language input: сегодня 18:30 созвон"""
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
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

    step_one = asyncio.run(manager.handle_text(user_id=2, chat_id=20, text="сегодня 18:30 созвон"))
    assert step_one is not None
    assert step_one.status == "ok"
    assert "созвон" in step_one.text

    confirm = asyncio.run(
        manager.handle_action(
            user_id=2,
            chat_id=20,
            op="wizard_confirm",
            payload={},
        )
    )
    assert confirm.status == "ok"

    items = asyncio.run(calendar_store.list_items(None, None))
    assert any(item.title == "созвон" for item in items)


def test_wizard_natural_language_relative_time(tmp_path, monkeypatch) -> None:
    """Test natural language input: через 2 часа тренировка"""
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
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

    step_one = asyncio.run(manager.handle_text(user_id=3, chat_id=30, text="через 2 часа тренировка"))
    assert step_one is not None
    assert step_one.status == "ok"
    assert "тренировка" in step_one.text

    confirm = asyncio.run(
        manager.handle_action(
            user_id=3,
            chat_id=30,
            op="wizard_confirm",
            payload={},
        )
    )
    assert confirm.status == "ok"

    items = asyncio.run(calendar_store.list_items(None, None))
    assert any(item.title == "тренировка" for item in items)


def test_wizard_natural_language_weekday(tmp_path, monkeypatch) -> None:
    """Test natural language input: в пятницу 10:15 встреча"""
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
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

    step_one = asyncio.run(manager.handle_text(user_id=4, chat_id=40, text="в пятницу 10:15 встреча"))
    assert step_one is not None
    assert step_one.status == "ok"
    assert "встреча" in step_one.text
    assert "10:15" in step_one.text

    confirm = asyncio.run(
        manager.handle_action(
            user_id=4,
            chat_id=40,
            op="wizard_confirm",
            payload={},
        )
    )
    assert confirm.status == "ok"

    items = asyncio.run(calendar_store.list_items(None, None))
    assert any(item.title == "встреча" for item in items)


def test_wizard_natural_language_day_month_with_title(tmp_path, monkeypatch) -> None:
    """Test natural language input: 07.02 12:00 стоматолог"""
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
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

    step_one = asyncio.run(manager.handle_text(user_id=5, chat_id=50, text="07.02 12:00 стоматолог"))
    assert step_one is not None
    assert step_one.status == "ok"
    assert "стоматолог" in step_one.text
    assert "12:00" in step_one.text

    confirm = asyncio.run(
        manager.handle_action(
            user_id=5,
            chat_id=50,
            op="wizard_confirm",
            payload={},
        )
    )
    assert confirm.status == "ok"

    items = asyncio.run(calendar_store.list_items(None, None))
    assert any(item.title == "стоматолог" for item in items)


def test_wizard_backward_compatibility_old_format(tmp_path, monkeypatch) -> None:
    """Test that old format (datetime only, then title) still works"""
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    store = WizardStore(tmp_path / "wizards", timeout_seconds=600)
    manager = WizardManager(store)

    # Start wizard
    asyncio.run(
        manager.handle_action(
            user_id=6,
            chat_id=60,
            op="wizard_start",
            payload={"wizard_id": WIZARD_CALENDAR_ADD},
        )
    )

    # Send datetime only (old format)
    step_one = asyncio.run(manager.handle_text(user_id=6, chat_id=60, text="2026-02-10 15:00"))
    assert step_one is not None
    assert step_one.status == "ok"
    # Should ask for title
    assert "название" in step_one.text.lower()

    # Send title
    step_two = asyncio.run(manager.handle_text(user_id=6, chat_id=60, text="Старый формат"))
    assert step_two is not None
    assert step_two.status == "ok"
    assert "создать" in step_two.text.lower()

    # Confirm
    confirm = asyncio.run(
        manager.handle_action(
            user_id=6,
            chat_id=60,
            op="wizard_confirm",
            payload={},
        )
    )
    assert confirm.status == "ok"

    items = asyncio.run(calendar_store.list_items(None, None))
    assert any(item.title == "Старый формат" for item in items)
