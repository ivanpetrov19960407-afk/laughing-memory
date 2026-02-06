from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.bot import wizard
from app.bot.wizard import WizardManager
from app.core import calendar_store
from app.storage.wizard_store import WizardStore


@dataclass
class DummySettings:
    reminders_enabled: bool = True


def test_reschedule_wizard_updates_trigger(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.MOSCOW_TZ)
    item = asyncio_run(
        calendar_store.add_item(
            dt=now + timedelta(hours=2),
            title="Call",
            chat_id=1,
            remind_at=now + timedelta(minutes=20),
            user_id=1,
        )
    )
    reminder_id = item["reminder"]["reminder_id"]
    store = WizardStore(tmp_path / "wizards")
    manager = WizardManager(store, reminder_scheduler=None, settings=DummySettings())
    start = asyncio_run(manager.start_reminder_reschedule(user_id=1, chat_id=1, reminder_id=reminder_id))
    assert start.status == "ok"
    step = asyncio_run(manager.handle_text(user_id=1, chat_id=1, text="2026-02-05 12:30"))
    assert step.status == "ok"
    confirm = asyncio_run(manager.handle_action(user_id=1, chat_id=1, op="wizard_confirm", payload={}))
    assert confirm.status == "ok"
    reminder = asyncio_run(calendar_store.get_reminder(reminder_id))
    assert reminder is not None
    assert reminder.trigger_at == datetime(2026, 2, 5, 12, 30, tzinfo=calendar_store.MOSCOW_TZ)


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def test_create_wizard_creates_reminder(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    store = WizardStore(tmp_path / "wizards")
    manager = WizardManager(store, reminder_scheduler=None, settings=DummySettings())

    started = asyncio_run(manager.handle_action(user_id=7, chat_id=9, op="wizard_start", payload={"wizard_id": wizard.WIZARD_REMINDER_CREATE}))
    assert started.status == "ok"
    step1 = asyncio_run(manager.handle_text(user_id=7, chat_id=9, text="Позвонить маме"))
    assert step1.status == "ok"
    step2 = asyncio_run(manager.handle_text(user_id=7, chat_id=9, text="2026-02-05 12:30"))
    assert step2.status == "ok"
    done = asyncio_run(manager.handle_action(user_id=7, chat_id=9, op="wizard_confirm", payload={}))
    assert done.status == "ok"
    reminders = asyncio_run(calendar_store.list_reminders(datetime(2026, 2, 5, 11, 0, tzinfo=calendar_store.MOSCOW_TZ), limit=10))
    assert len(reminders) == 1
    assert reminders[0].text == "Позвонить маме"
    assert reminders[0].recurrence is None
