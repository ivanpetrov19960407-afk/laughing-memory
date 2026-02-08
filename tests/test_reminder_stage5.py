from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.bot import actions, handlers, wizard
from app.core import calendar_store
from app.core.result import Action
from app.storage.wizard_store import WizardStore


class DummyContext:
    def __init__(self) -> None:
        self.application = SimpleNamespace(
            bot_data={
                "action_store": actions.ActionStore(),
                "settings": SimpleNamespace(enable_menu=True, enable_wizards=True, strict_no_pseudo_sources=True, reminders_enabled=True),
            }
        )
        self.chat_data: dict[str, object] = {}


class DummyUpdate:
    def __init__(self, user_id: int = 1, chat_id: int = 10) -> None:
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.message = SimpleNamespace(message_id=1, text="/menu")
        self.effective_message = self.message
        self.callback_query = None


def test_reminder_snooze_shifts_trigger(tmp_path, monkeypatch) -> None:
    class DummyScheduler:
        def __init__(self) -> None:
            self.scheduled: str | None = None

        async def schedule_reminder(self, reminder) -> None:
            self.scheduled = reminder.id

    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))

    now = datetime.now(tz=calendar_store.BOT_TZ)
    reminder = asyncio.run(
        calendar_store.add_reminder(
            trigger_at=now + timedelta(hours=1),
            text="Ping",
            chat_id=10,
            user_id=1,
        )
    )

    context = DummyContext()
    context.application.bot_data["reminder_scheduler"] = DummyScheduler()
    result = asyncio.run(
        handlers._handle_reminder_snooze(
            context,
            user_id=1,
            reminder_id=reminder.id,
            minutes=30,
            base_trigger_at=reminder.trigger_at.isoformat(),
        )
    )

    assert result.status == "ok"
    updated = asyncio.run(calendar_store.get_reminder(reminder.id))
    assert updated is not None
    assert updated.trigger_at == reminder.trigger_at + timedelta(minutes=30)


def test_reminder_delete_via_action(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        return True

    async def fake_answer():
        return None

    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))

    reminder = asyncio.run(
        calendar_store.add_reminder(
            trigger_at=datetime.now(tz=calendar_store.BOT_TZ) + timedelta(hours=1),
            text="Ping",
            chat_id=10,
            user_id=1,
        )
    )

    update = DummyUpdate()
    context = DummyContext()
    store = context.application.bot_data["action_store"]
    action = Action(
        id="utility_reminders.delete",
        label="Delete",
        payload={"op": "reminder.delete_confirm", "reminder_id": reminder.id},
    )
    action_id = store.store_action(action=action, user_id=1, chat_id=10)
    update.callback_query = SimpleNamespace(data=f"a:{action_id}", answer=fake_answer)

    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    asyncio.run(handlers.action_callback(update, context))

    confirm_action = next(action for action in captured["result"].actions if action.payload.get("op") == "reminder.delete_confirmed")
    confirm_id = store.store_action(action=confirm_action, user_id=1, chat_id=10)
    update.callback_query = SimpleNamespace(data=f"a:{confirm_id}", answer=fake_answer)

    asyncio.run(handlers.action_callback(update, context))

    reminders = asyncio.run(
        calendar_store.list_reminders(datetime.now(tz=calendar_store.BOT_TZ), limit=10)
    )
    assert all(item.id != reminder.id for item in reminders)


def test_reminder_reschedule_flow(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))

    reminder = asyncio.run(
        calendar_store.add_reminder(
            trigger_at=datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.BOT_TZ),
            text="Ping",
            chat_id=10,
            user_id=1,
        )
    )

    store = WizardStore(tmp_path / "wizards")
    manager = wizard.WizardManager(store, reminder_scheduler=None, settings=SimpleNamespace(reminders_enabled=True))

    start = asyncio.run(manager.start_reminder_reschedule(user_id=1, chat_id=10, reminder_id=reminder.id))
    assert start.intent == "wizard.reminder.datetime"

    text_result = asyncio.run(
        manager.handle_text(user_id=1, chat_id=10, text="2026-02-05 12:30")
    )
    assert text_result is not None
    assert text_result.intent == "wizard.reminder.confirm"

    confirm = asyncio.run(
        manager.handle_action(user_id=1, chat_id=10, op="wizard_confirm", payload={})
    )
    assert confirm is not None
    assert confirm.intent == "wizard.reminder.done"

    updated = asyncio.run(calendar_store.get_reminder(reminder.id))
    assert updated is not None
    assert updated.trigger_at == datetime(2026, 2, 5, 12, 30, tzinfo=calendar_store.BOT_TZ)


def test_recurring_reminder_creates_next_trigger(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))

    reminder = asyncio.run(
        calendar_store.add_reminder(
            trigger_at=datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.BOT_TZ),
            text="Daily ping",
            chat_id=10,
            user_id=1,
            recurrence={"freq": "daily"},
        )
    )

    next_reminder = asyncio.run(
        calendar_store.mark_reminder_sent(reminder.id, datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.BOT_TZ))
    )
    assert next_reminder is not None
    assert next_reminder.trigger_at == datetime(2026, 2, 6, 10, 0, tzinfo=calendar_store.BOT_TZ)
