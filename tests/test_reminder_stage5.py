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


def test_reminder_snooze_10_minutes_direct(tmp_path, monkeypatch) -> None:
    """Test that snooze 10 minutes button works directly without menu."""
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
            text="Test reminder",
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
            minutes=10,
            base_trigger_at=reminder.trigger_at.isoformat(),
        )
    )

    assert result.status == "ok"
    updated = asyncio.run(calendar_store.get_reminder(reminder.id))
    assert updated is not None
    assert updated.trigger_at == reminder.trigger_at + timedelta(minutes=10)


def test_reminder_snooze_1_hour_direct(tmp_path, monkeypatch) -> None:
    """Test that snooze 1 hour button works directly without menu."""
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
            text="Test reminder",
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
            minutes=60,
            base_trigger_at=reminder.trigger_at.isoformat(),
        )
    )

    assert result.status == "ok"
    updated = asyncio.run(calendar_store.get_reminder(reminder.id))
    assert updated is not None
    assert updated.trigger_at == reminder.trigger_at + timedelta(minutes=60)


def test_reminders_list_24h(tmp_path, monkeypatch) -> None:
    """Test that list_24h shows only reminders within next 24 hours."""
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))

    now = datetime.now(tz=calendar_store.BOT_TZ)
    
    # Create reminders: one within 24h, one beyond 24h
    reminder_soon = asyncio.run(
        calendar_store.add_reminder(
            trigger_at=now + timedelta(hours=12),
            text="Soon reminder",
            chat_id=10,
            user_id=1,
        )
    )
    
    reminder_later = asyncio.run(
        calendar_store.add_reminder(
            trigger_at=now + timedelta(hours=30),
            text="Later reminder",
            chat_id=10,
            user_id=1,
        )
    )

    context = DummyContext()
    result = asyncio.run(
        handlers._handle_reminders_list_24h(
            context,
            user_id=1,
            chat_id=10,
            intent="utility_reminders.list_24h",
        )
    )

    assert result.status == "ok"
    assert "Soon reminder" in result.text
    assert "Later reminder" not in result.text
    
    # Check that actions are present for the reminder
    action_ids = [action.id for action in result.actions]
    assert any(f"reminder_snooze:{reminder_soon.id}:10" in aid for aid in action_ids)
    assert any(f"reminder_snooze:{reminder_soon.id}:60" in aid for aid in action_ids)


def test_reminders_list_24h_empty(tmp_path, monkeypatch) -> None:
    """Test that list_24h shows empty message when no reminders."""
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))

    context = DummyContext()
    result = asyncio.run(
        handlers._handle_reminders_list_24h(
            context,
            user_id=1,
            chat_id=10,
            intent="utility_reminders.list_24h",
        )
    )

    assert result.status == "ok"
    assert "На сегодня напоминаний нет" in result.text


def test_reminder_delete_confirms_and_deletes(tmp_path, monkeypatch) -> None:
    """Test that delete requires confirmation and then actually deletes."""
    class DummyScheduler:
        def __init__(self) -> None:
            self.cancelled: str | None = None

        async def cancel_reminder(self, reminder_id: str) -> bool:
            self.cancelled = reminder_id
            return True

    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))

    reminder = asyncio.run(
        calendar_store.add_reminder(
            trigger_at=datetime.now(tz=calendar_store.BOT_TZ) + timedelta(hours=1),
            text="To delete",
            chat_id=10,
            user_id=1,
        )
    )

    context = DummyContext()
    context.application.bot_data["reminder_scheduler"] = DummyScheduler()
    
    # First, confirm deletion - use the handler directly
    update = DummyUpdate()
    result = asyncio.run(
        handlers._dispatch_action_payload(
            update,
            context,
            op="reminder.delete_confirm",
            payload={"reminder_id": reminder.id},
            intent="utility_reminders.delete",
        )
    )
    assert result.status == "ok"
    assert "Удалить напоминание" in result.text
    
    # Then actually delete
    delete_result = asyncio.run(
        handlers._handle_reminder_delete(
            context,
            reminder_id=reminder.id,
            user_id=1,
            chat_id=10,
        )
    )
    assert delete_result.status == "ok"
    assert "Напоминание удалено" in delete_result.text
    
    # Verify it's gone
    reminders = asyncio.run(
        calendar_store.list_reminders(datetime.now(tz=calendar_store.BOT_TZ), limit=10)
    )
    assert all(item.id != reminder.id for item in reminders)
    assert context.application.bot_data["reminder_scheduler"].cancelled == reminder.id
