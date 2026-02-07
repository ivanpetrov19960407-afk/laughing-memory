from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.bot import handlers, wizard
from app.bot.actions import ActionStore
from app.core import calendar_store
from app.core.memory_store import MemoryStore, build_llm_context
from app.core.recurrence_scope import RecurrenceScope
from app.core.reminders import ReminderScheduler
from app.core.tools_calendar import create_event
from app.infra.request_context import RequestContext
from app.storage.wizard_store import WizardStore


@dataclass
class DummyJob:
    name: str
    removed: bool = False

    def schedule_removal(self) -> None:
        self.removed = True


class DummyJobQueue:
    def __init__(self) -> None:
        self.jobs: dict[str, list[DummyJob]] = {}

    def run_once(self, callback, when, name: str, data: dict) -> DummyJob:
        _ = callback
        _ = when
        _ = data
        job = DummyJob(name=name)
        self.jobs.setdefault(name, []).append(job)
        return job

    def get_jobs_by_name(self, name: str) -> list[DummyJob]:
        return list(self.jobs.get(name, []))


def asyncio_run(coro):
    return asyncio.run(coro)


@pytest.fixture
def calendar_env(tmp_path, monkeypatch) -> str:
    path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(path))
    monkeypatch.setenv("CALENDAR_BACKEND", "local")
    return str(path)


def _build_context() -> SimpleNamespace:
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "action_store": ActionStore(),
                "settings": SimpleNamespace(
                    enable_menu=True,
                    enable_wizards=True,
                    strict_no_pseudo_sources=True,
                    reminders_enabled=True,
                ),
            }
        ),
        chat_data={},
    )


def _build_update(text: str) -> SimpleNamespace:
    message = SimpleNamespace(text=text, caption=None, message_id=1)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=1, username="tester"),
        effective_chat=SimpleNamespace(id=10, type="private"),
        message=message,
        effective_message=message,
        callback_query=None,
    )


def _json_records(caplog, logger_name: str) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for record in caplog.records:
        if record.name != logger_name:
            continue
        try:
            entries.append(json.loads(record.message))
        except json.JSONDecodeError:
            continue
    return entries


def _create_recurring_event(*, start_at: datetime, recurrence_text: str) -> str:
    result = asyncio_run(
        create_event(
            start_at=start_at,
            title="Стендап",
            chat_id=1,
            user_id=1,
            recurrence_text=recurrence_text,
        )
    )
    assert result.status == "ok"
    assert isinstance(result.debug, dict)
    event_id = result.debug.get("event_id")
    assert isinstance(event_id, str)
    return event_id


def test_unknown_command_fallback_no_traceback(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        _ = update
        _ = context
        _ = reply_markup
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        _ = update
        _ = context
        _ = bucket
        return True

    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    update = _build_update("/unknown")
    context = _build_context()

    asyncio_run(handlers.unknown_command(update, context))

    result = captured["result"]
    assert result.status == "refused"
    assert "Traceback" not in result.text


def test_menu_command_has_actions_and_text(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        _ = update
        _ = context
        _ = reply_markup
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        _ = update
        _ = context
        _ = bucket
        return True

    async def fake_remove(update, context, text="Открываю меню…"):
        _ = update
        _ = context
        _ = text
        return None

    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)
    monkeypatch.setattr(handlers, "_send_reply_keyboard_remove", fake_remove)

    update = _build_update("/menu")
    context = _build_context()
    context.application.bot_data["orchestrator"] = SimpleNamespace(is_facts_only=lambda user_id: False)

    asyncio_run(handlers.menu_command(update, context))

    result = captured["result"]
    assert result.text
    assert result.actions


def test_reminder_create_wizard_ok(calendar_env, tmp_path) -> None:
    _ = calendar_env
    store = WizardStore(tmp_path / "wizard")
    manager = wizard.WizardManager(store)

    start = asyncio_run(
        manager.handle_action(
            user_id=1,
            chat_id=2,
            op="wizard_start",
            payload={"wizard_id": wizard.WIZARD_REMINDER_CREATE},
        )
    )
    assert start and start.status == "ok"

    step_title = asyncio_run(manager.handle_text(user_id=1, chat_id=2, text="Позвонить маме"))
    assert step_title and step_title.status == "ok"

    step_dt = asyncio_run(manager.handle_text(user_id=1, chat_id=2, text="2026-02-05 12:30"))
    assert step_dt and step_dt.status == "ok"

    confirm = asyncio_run(
        manager.handle_action(
            user_id=1,
            chat_id=2,
            op="wizard_confirm",
            payload={},
        )
    )
    assert confirm and confirm.status == "ok"
    assert "Ок" in confirm.text
    assert isinstance(confirm.debug, dict)
    assert "reminder_id" in confirm.debug.get("refs", {})


def test_reminder_restore_after_reload(calendar_env) -> None:
    _ = calendar_env
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.MOSCOW_TZ)
    reminder = asyncio_run(
        calendar_store.add_reminder(
            trigger_at=now + timedelta(hours=2),
            text="Follow up",
            chat_id=1,
            user_id=1,
        )
    )
    job_queue = DummyJobQueue()
    application = SimpleNamespace(job_queue=job_queue, bot=SimpleNamespace())
    scheduler = ReminderScheduler(application=application)

    restored = asyncio_run(scheduler.restore_all(now))

    assert restored == 1
    assert scheduler._job_name(reminder.id) in job_queue.jobs


def test_calendar_create_explicit_date_ok(calendar_env) -> None:
    _ = calendar_env
    start_at = datetime(2026, 2, 5, 12, 0, tzinfo=calendar_store.BOT_TZ)
    result = asyncio_run(
        create_event(start_at=start_at, title="Врач", chat_id=1, user_id=1)
    )
    assert result.status == "ok"


def test_calendar_create_human_date_ok(calendar_env) -> None:
    _ = calendar_env
    start_at, title = calendar_store.parse_event_datetime("завтра 10 Встреча")
    result = asyncio_run(
        create_event(start_at=start_at, title=title or "Встреча", chat_id=1, user_id=1)
    )
    assert result.status == "ok"


def test_recurrence_weekdays_rrule(calendar_env) -> None:
    _ = calendar_env
    start_at = datetime(2026, 2, 5, 9, 0, tzinfo=calendar_store.BOT_TZ)
    result = asyncio_run(
        create_event(
            start_at=start_at,
            title="Стендап",
            chat_id=1,
            user_id=1,
            recurrence_text="каждый будний день в 9",
        )
    )
    assert result.status == "ok"
    assert isinstance(result.debug, dict)
    rrule = result.debug.get("rrule")
    assert isinstance(rrule, str)
    assert "BYDAY=MO,TU,WE,TH,FR" in rrule


def test_recurrence_until_rrule(calendar_env) -> None:
    _ = calendar_env
    start_at = datetime(2026, 2, 6, 9, 0, tzinfo=calendar_store.BOT_TZ)
    result = asyncio_run(
        create_event(
            start_at=start_at,
            title="Отчёт",
            chat_id=1,
            user_id=1,
            recurrence_text="каждую пятницу до 31.03.2026",
        )
    )
    assert result.status == "ok"
    assert isinstance(result.debug, dict)
    rrule = result.debug.get("rrule")
    assert isinstance(rrule, str)
    assert "UNTIL=" in rrule


def test_recurrence_exdates_saved(calendar_env) -> None:
    _ = calendar_env
    start_at = datetime(2026, 3, 1, 9, 0, tzinfo=calendar_store.BOT_TZ)
    event_id = _create_recurring_event(
        start_at=start_at,
        recurrence_text="каждый день кроме 08.03.2026",
    )
    event = asyncio_run(calendar_store.get_event(event_id))
    assert event is not None
    assert event.exdates
    assert any(exdate.date() == date(2026, 3, 8) for exdate in event.exdates)


def test_update_recurring_without_scope_actions(calendar_env) -> None:
    _ = calendar_env
    start_at = datetime(2026, 2, 5, 9, 0, tzinfo=calendar_store.BOT_TZ)
    event_id = _create_recurring_event(
        start_at=start_at,
        recurrence_text="каждый день",
    )
    context = _build_context()

    result = asyncio_run(
        handlers._handle_event_move_tomorrow(
            context,
            event_id=event_id,
            user_id=1,
            chat_id=1,
        )
    )

    assert result.status == "ok"
    scopes = {action.payload.get("scope") for action in result.actions}
    assert scopes == {"THIS", "ALL", "FUTURE"}


def test_update_recurring_with_scope_all_ok(calendar_env) -> None:
    _ = calendar_env
    start_at = datetime(2026, 2, 5, 9, 0, tzinfo=calendar_store.BOT_TZ)
    event_id = _create_recurring_event(
        start_at=start_at,
        recurrence_text="каждый день",
    )
    context = _build_context()

    result = asyncio_run(
        handlers._handle_event_move_tomorrow(
            context,
            event_id=event_id,
            user_id=1,
            chat_id=1,
            scope=RecurrenceScope.ALL,
        )
    )

    assert result.status == "ok"
    assert result.debug.get("refs", {}).get("event_id") == event_id


def test_delete_recurring_scope_this_ok(calendar_env) -> None:
    _ = calendar_env
    start_at = datetime(2026, 2, 5, 9, 0, tzinfo=calendar_store.BOT_TZ)
    event_id = _create_recurring_event(
        start_at=start_at,
        recurrence_text="каждый день",
    )
    context = _build_context()

    result = asyncio_run(
        handlers._handle_event_delete(
            context,
            event_id=event_id,
            scope=RecurrenceScope.THIS,
            instance_dt=start_at,
            user_id=1,
        )
    )

    assert result.status == "ok"
    assert result.debug.get("event_id") == event_id


def test_observability_trace_summary_and_response_sent(monkeypatch, caplog) -> None:
    async def fake_guard_access(update, context, bucket="default"):
        _ = update
        _ = context
        _ = bucket
        return True

    async def fake_send_text(update, context, text, reply_markup=None):
        _ = update
        _ = context
        _ = text
        _ = reply_markup
        return None

    async def fake_send_attachments(update, context, attachments):
        _ = update
        _ = context
        _ = attachments
        return None

    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)
    monkeypatch.setattr(handlers, "_send_text", fake_send_text)
    monkeypatch.setattr(handlers, "_send_attachments", fake_send_attachments)

    update = _build_update("/unknown")
    context = _build_context()

    caplog.set_level(logging.INFO, logger="app.bot.handlers")
    asyncio_run(handlers.unknown_command(update, context))

    events = _json_records(caplog, "app.bot.handlers")
    summary = next(entry for entry in events if entry.get("event") == "trace.summary")
    response = next(entry for entry in events if entry.get("event") == "response.sent")

    assert summary.get("correlation_id")
    assert response.get("correlation_id")
    assert summary["correlation_id"] == response["correlation_id"]


def test_memory_used_event_logged(caplog) -> None:
    store = MemoryStore(max_items=5, ttl_seconds=3600)
    store.add(
        chat_id=2,
        user_id=1,
        role="user",
        kind="message",
        content="Первый вопрос",
        env="dev",
        correlation_id="corr-0",
    )
    store.add(
        chat_id=2,
        user_id=1,
        role="assistant",
        kind="result",
        content="Первый ответ",
        intent="intent.one",
        status="ok",
        env="dev",
        correlation_id="corr-0",
    )
    request_context = RequestContext(
        correlation_id="corr-1",
        user_id=1,
        chat_id=2,
        message_id=3,
        timezone=None,
        ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
        env="dev",
    )

    caplog.set_level(logging.INFO, logger="app.core.memory_store")
    context = build_llm_context(request_context, store, limit=10, max_chars=200)

    assert context is not None
    events = _json_records(caplog, "app.core.memory_store")
    assert any(event.get("event") == "memory.used" for event in events)
