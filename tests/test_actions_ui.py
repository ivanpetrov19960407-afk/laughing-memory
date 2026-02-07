from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import telegram

from app.bot import actions, handlers, menu, wizard
from app.core import calendar_store
from app.core.result import Action, ok
from app.infra.request_context import start_request
from app.infra.rate_limiter import RateLimiter
from app.storage.wizard_store import WizardStore


class DummyContext:
    def __init__(self) -> None:
        self.application = SimpleNamespace(
            bot_data={
                "action_store": actions.ActionStore(),
                "ui_rate_limiter": RateLimiter(),
                "rate_limiter": RateLimiter(),
                "settings": SimpleNamespace(enable_menu=True, enable_wizards=False, strict_no_pseudo_sources=True),
            }
        )
        self.chat_data: dict[str, object] = {}


class DummyUpdate:
    def __init__(self, user_id: int = 1, chat_id: int = 10) -> None:
        self.effective_user = SimpleNamespace(id=user_id, username="tester")
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.message = SimpleNamespace(text="/menu")
        self.effective_message = self.message
        self.callback_query = None


def test_action_store_ttl_and_security(monkeypatch) -> None:
    clock = {"value": 100.0}

    def fake_monotonic() -> float:
        return clock["value"]

    monkeypatch.setattr(actions.time, "monotonic", fake_monotonic)
    store = actions.ActionStore(ttl_seconds=5, max_items=10)
    action = Action(id="test", label="Test", payload={"op": "menu_open"})
    action_id = store.store_action(action=action, user_id=1, chat_id=2)

    assert store.get_action(user_id=1, chat_id=999, action_id=action_id) is None
    stored = store.get_action(user_id=1, chat_id=2, action_id=action_id)
    assert stored is not None
    assert stored.payload["op"] == "menu_open"
    assert stored.intent == "test"
    stored_second = store.get_action(user_id=1, chat_id=2, action_id=action_id)
    assert stored_second is not None
    assert stored_second.intent == "test"

    action_id_expired = store.store_action(action=action, user_id=1, chat_id=2)
    clock["value"] += 10
    assert store.get_action(user_id=1, chat_id=2, action_id=action_id_expired) is None


def test_build_inline_keyboard() -> None:
    store = actions.ActionStore()
    action_list = [
        Action(id="one", label="One", payload={"op": "run_command", "command": "/help"}),
        Action(id="two", label="Two", payload={"op": "run_command", "command": "/menu"}),
    ]
    keyboard = actions.build_inline_keyboard(action_list, store=store, user_id=1, chat_id=2)
    assert keyboard is not None
    buttons = keyboard.inline_keyboard
    assert buttons[0][0].text == "One"
    assert buttons[0][0].callback_data.startswith(actions.CALLBACK_PREFIX)


def test_menu_actions_use_static_callbacks() -> None:
    store = actions.ActionStore()
    action_list = menu.build_menu_actions(facts_enabled=False, enable_menu=True)
    keyboard = actions.build_inline_keyboard(action_list, store=store, user_id=1, chat_id=2)
    assert keyboard is not None
    callback_values = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert all(value.startswith(actions.STATIC_CALLBACK_PREFIX) for value in callback_values)


def test_menu_command_returns_actions(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        return True

    async def fake_remove(update, context, text="Открываю меню…"):
        return None

    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)
    monkeypatch.setattr(handlers, "_send_reply_keyboard_remove", fake_remove)

    update = DummyUpdate()
    context = DummyContext()
    context.application.bot_data["orchestrator"] = SimpleNamespace(is_facts_only=lambda user_id: False)
    asyncio.run(handlers.menu_command(update, context))

    result = captured["result"]
    assert result.actions


def test_expired_action_returns_refused_text(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        return True

    async def fake_answer():
        return None

    update = DummyUpdate()
    update.callback_query = SimpleNamespace(data="a:missing", answer=fake_answer)
    context = DummyContext()

    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    asyncio.run(handlers.action_callback(update, context))
    result = captured["result"]
    assert result.status == "refused"
    assert "Кнопка устарела" in result.text


def test_callback_logs_single_orchestrator_result(monkeypatch, caplog) -> None:
    calls: list[str] = []

    async def fake_send_text(update, context, text, reply_markup=None):
        calls.append(text)

    async def fake_guard_access(update, context, bucket="default"):
        return True

    async def fake_answer():
        return None

    async def fake_dispatch_action(update, context, op, payload, intent):
        return ok("Health: OK", intent="menu.status", mode="local")

    update = DummyUpdate()
    context = DummyContext()
    store = context.application.bot_data["action_store"]
    action = Action(id="menu.status", label="Status", payload={"op": "run_command", "command": "/health"})
    action_id = store.store_action(action=action, user_id=1, chat_id=10)
    update.callback_query = SimpleNamespace(data=f"a:{action_id}", answer=fake_answer)

    caplog.set_level("INFO")
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)
    monkeypatch.setattr(handlers, "_dispatch_action_payload", fake_dispatch_action)
    monkeypatch.setattr(handlers, "_send_text", fake_send_text)

    asyncio.run(handlers.action_callback(update, context))
    matches = [record for record in caplog.records if "Orchestrator result:" in record.getMessage()]
    assert len(matches) == 1


def test_callback_aliases_calendar_list_intent(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        return True

    async def fake_answer():
        return None

    async def fake_dispatch_action(update, context, op, payload, intent):
        captured["dispatch_intent"] = intent
        return ok("List", intent="menu.calendar", mode="local")

    def fake_set_input_text(context, text):
        captured["input_text"] = text

    update = DummyUpdate()
    context = DummyContext()
    store = context.application.bot_data["action_store"]
    action = Action(id="calendar.list", label="List", payload={"op": "run_command", "command": "/calendar list", "args": ""})
    action_id = store.store_action(action=action, user_id=1, chat_id=10)
    update.callback_query = SimpleNamespace(data=f"a:{action_id}", answer=fake_answer)

    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)
    monkeypatch.setattr(handlers, "_dispatch_action_payload", fake_dispatch_action)
    monkeypatch.setattr(handlers, "set_input_text", fake_set_input_text)

    asyncio.run(handlers.action_callback(update, context))

    result = captured["result"]
    assert captured["dispatch_intent"] == "utility_calendar.list"
    assert captured["input_text"] == "<callback:utility_calendar.list>"
    assert result.intent == "utility_calendar.list"


def test_callback_calendar_delete_removes_event(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        return True

    async def fake_answer():
        return None

    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_BACKEND", "local")

    event = asyncio.run(
        calendar_store.add_item(
            dt=datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.BOT_TZ),
            title="Test",
            chat_id=10,
            user_id=1,
            reminders_enabled=False,
        )
    )
    event_id = event["event"]["event_id"]

    update = DummyUpdate()
    context = DummyContext()
    store = context.application.bot_data["action_store"]
    action = Action(
        id="utility_calendar.delete",
        label="Delete",
        payload={"op": "calendar.delete", "event_id": event_id},
    )
    action_id = store.store_action(action=action, user_id=1, chat_id=10)
    update.callback_query = SimpleNamespace(data=f"a:{action_id}", answer=fake_answer)

    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    asyncio.run(handlers.action_callback(update, context))

    result = captured["result"]
    assert result.status == "ok"
    assert result.intent == "utility_calendar.delete"
    removed = asyncio.run(calendar_store.get_event(event_id))
    assert removed is None


def test_callback_reminder_delete_disables_reminder(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        return True

    async def fake_answer():
        return None

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

    result = captured["result"]
    assert result.status == "ok"
    assert result.intent == "utility_reminders.delete"
    confirm_action = next(action for action in result.actions if action.payload.get("op") == "reminder.delete_confirmed")
    confirm_id = store.store_action(action=confirm_action, user_id=1, chat_id=10)
    update.callback_query = SimpleNamespace(data=f"a:{confirm_id}", answer=fake_answer)

    asyncio.run(handlers.action_callback(update, context))

    result = captured["result"]
    assert result.status == "ok"
    assert result.intent == "utility_reminders.delete"
    reminders = asyncio.run(
        calendar_store.list_reminders(datetime(2026, 2, 5, 11, 0, tzinfo=calendar_store.BOT_TZ), limit=10)
    )
    assert all(item.id != reminder.id for item in reminders)


def test_callback_reminder_snooze_updates_schedule(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}
    scheduled: dict[str, object] = {}

    class DummyScheduler:
        async def schedule_reminder(self, reminder):
            scheduled["reminder_id"] = reminder.id
            scheduled["trigger_at"] = reminder.trigger_at

    async def fake_send_result(update, context, result, reply_markup=None):
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        return True

    async def fake_answer():
        return None

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

    update = DummyUpdate()
    context = DummyContext()
    context.application.bot_data["reminder_scheduler"] = DummyScheduler()
    context.application.bot_data["settings"] = SimpleNamespace(reminders_enabled=True)
    store = context.application.bot_data["action_store"]
    action = Action(
        id="reminder_snooze",
        label="Snooze",
        payload={
            "op": "reminder_snooze",
            "reminder_id": reminder.id,
            "minutes": 10,
            "base_trigger_at": reminder.trigger_at.isoformat(),
        },
    )
    action_id = store.store_action(action=action, user_id=1, chat_id=10)
    update.callback_query = SimpleNamespace(data=f"a:{action_id}", answer=fake_answer)

    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    asyncio.run(handlers.action_callback(update, context))

    result = captured["result"]
    assert result.status == "ok"
    assert result.intent == "utility_reminders.snooze"
    updated = asyncio.run(calendar_store.get_reminder(reminder.id))
    assert updated is not None
    assert updated.trigger_at == reminder.trigger_at + timedelta(minutes=10)
    assert scheduled["reminder_id"] == reminder.id


def test_resume_prompt_restart_starts_reminder_wizard(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        return True

    async def fake_answer(text=None):
        return None

    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))

    store = WizardStore(tmp_path / "wizards")
    manager = wizard.WizardManager(store, reminder_scheduler=None, settings=SimpleNamespace(reminders_enabled=True))

    update = DummyUpdate()
    context = DummyContext()
    context.application.bot_data["wizard_manager"] = manager
    context.application.bot_data["settings"] = SimpleNamespace(enable_menu=True, enable_wizards=True, strict_no_pseudo_sources=True)

    asyncio.run(
        manager.handle_action(user_id=1, chat_id=10, op="wizard_start", payload={"wizard_id": wizard.WIZARD_CALENDAR_ADD})
    )
    resume_prompt = asyncio.run(
        handlers._dispatch_action_payload(
            update,
            context,
            op="reminder.create",
            payload={"op": "reminder.create"},
            intent="utility_reminders.create",
        )
    )
    restart_action = next(action for action in resume_prompt.actions if action.id == "wizard.restart")
    action_id = context.application.bot_data["action_store"].store_action(action=restart_action, user_id=1, chat_id=10)
    update.callback_query = SimpleNamespace(data=f"a:{action_id}", answer=fake_answer)

    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    asyncio.run(handlers.action_callback(update, context))

    result = captured["result"]
    assert result.intent.startswith("wizard.reminder_create.")
    state, expired = manager.get_state(user_id=1, chat_id=10)
    assert expired is False
    assert state is not None
    assert state.wizard_id == wizard.WIZARD_REMINDER_CREATE


def test_send_result_deduplicates(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_send_text(update, context, text, reply_markup=None):
        calls.append(text)

    update = DummyUpdate()
    context = DummyContext()
    context.application.bot_data["orchestrator"] = SimpleNamespace(is_facts_only=lambda user_id: False)
    start_request(update, context)
    result = menu.build_menu_actions(facts_enabled=False, enable_menu=True)
    orchestrator_result = ok("Меню:", intent="command.menu", mode="local", actions=result)

    monkeypatch.setattr(handlers, "_send_text", fake_send_text)
    asyncio.run(handlers.send_result(update, context, orchestrator_result))
    asyncio.run(handlers.send_result(update, context, orchestrator_result))
    assert len(calls) == 1


def test_menu_open_sends_inline_keyboard(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_text(update, context, text, reply_markup=None):
        captured["reply_markup"] = reply_markup

    update = DummyUpdate()
    context = DummyContext()
    context.application.bot_data["orchestrator"] = SimpleNamespace(is_facts_only=lambda user_id: False)
    actions_list = menu.build_menu_actions(facts_enabled=False, enable_menu=True)
    result = ok("Меню:", intent="menu.open", mode="local", actions=actions_list)

    monkeypatch.setattr(handlers, "_send_text", fake_send_text)

    asyncio.run(handlers.send_result(update, context, result))

    reply_markup = captured["reply_markup"]
    assert isinstance(reply_markup, telegram.InlineKeyboardMarkup)


def test_menu_cancel_removes_reply_keyboard(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def fake_remove(update, context, text="Открываю меню…"):
        calls["text"] = text

    update = DummyUpdate()
    context = DummyContext()
    context.application.bot_data["orchestrator"] = SimpleNamespace(is_facts_only=lambda user_id: False)
    stored = actions.StoredAction(
        user_id=1,
        chat_id=10,
        intent="menu.cancel",
        payload={"op": "menu_cancel"},
        created_at=0.0,
        expires_at=100.0,
    )

    monkeypatch.setattr(handlers, "_send_reply_keyboard_remove", fake_remove)

    result = asyncio.run(handlers._dispatch_action(update, context, stored))
    assert calls["text"] == "Ок"
    assert result.text == "Ок"


def test_static_callback_routes_menu_open(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_dispatch(update, context, op, payload, intent):
        captured["op"] = op
        captured["payload"] = payload
        captured["intent"] = intent
        return ok("Меню:", intent="menu.open", mode="local")

    async def fake_send_result(update, context, result, reply_markup=None):
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        return True

    async def fake_answer():
        return None

    update = DummyUpdate()
    update.callback_query = SimpleNamespace(data="cb:menu:open", answer=fake_answer)
    context = DummyContext()

    monkeypatch.setattr(handlers, "_dispatch_action_payload", fake_dispatch)
    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    asyncio.run(handlers.static_callback(update, context))
    assert captured["op"] == "menu_open"
    assert captured["payload"] == {}
    assert captured["intent"] == "callback.menu.open"
    result = captured["result"]
    assert result.status == "ok"
