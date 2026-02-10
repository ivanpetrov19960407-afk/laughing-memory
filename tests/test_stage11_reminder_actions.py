from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.bot.actions import ActionStore, build_inline_keyboard
from app.bot import handlers
from app.core import calendar_store
from app.core.reminders import ReminderScheduler as OneShotReminderScheduler
from app.core.reminders import _build_reminder_actions as build_notification_actions


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
        job = DummyJob(name=name)
        self.jobs.setdefault(name, []).append(job)
        return job

    def get_jobs_by_name(self, name: str) -> list[DummyJob]:
        return list(self.jobs.get(name, []))


@pytest.fixture
def calendar_path(tmp_path, monkeypatch) -> str:
    path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(path))
    return str(path)


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def test_notification_keyboard_callback_data_is_static_and_safe(calendar_path) -> None:
    now = datetime.now(tz=calendar_store.BOT_TZ)
    reminder = asyncio_run(
        calendar_store.add_reminder(
            trigger_at=now + timedelta(hours=2),
            text="секретный текст пользователя",
            chat_id=10,
            user_id=7,
        )
    )
    actions = build_notification_actions(reminder)
    store = ActionStore()
    markup = build_inline_keyboard(actions, store=store, user_id=reminder.user_id, chat_id=reminder.chat_id, columns=2)
    assert markup is not None
    callback_data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert all(isinstance(value, str) and value.startswith("cb:") for value in callback_data)
    # Security: callback-data must not contain arbitrary user text.
    assert all("секретный" not in value for value in callback_data)


def test_snooze_now_shifts_from_now_and_reschedules_job(calendar_path) -> None:
    now = datetime.now(tz=calendar_store.BOT_TZ)
    reminder = asyncio_run(
        calendar_store.add_reminder(
            trigger_at=now + timedelta(days=2),
            text="Call",
            chat_id=1,
            user_id=1,
        )
    )
    job_queue = DummyJobQueue()
    application = SimpleNamespace(job_queue=job_queue, bot=SimpleNamespace(), bot_data={"settings": SimpleNamespace(reminders_enabled=True)})
    scheduler = OneShotReminderScheduler(application=application)
    application.bot_data["reminder_scheduler"] = scheduler
    context = SimpleNamespace(application=application)

    # schedule original job
    asyncio_run(scheduler.schedule_reminder(reminder))
    job_name = scheduler._job_name(reminder.id)
    assert job_name in job_queue.jobs
    original_jobs = list(job_queue.jobs[job_name])
    original_trigger = reminder.trigger_at

    # Snooze from notification uses "now" base; with far-future original trigger it should move earlier.
    result = asyncio_run(
        handlers._handle_reminder_snooze_now(  # type: ignore[attr-defined]
            context,
            user_id=reminder.user_id,
            chat_id=reminder.chat_id,
            reminder_id=reminder.id,
            minutes=5,
        )
    )
    assert result.status == "ok"
    updated = asyncio_run(calendar_store.get_reminder(reminder.id))
    assert updated is not None
    assert updated.trigger_at < original_trigger
    # scheduler should add another job and mark old ones for removal
    assert job_name in job_queue.jobs
    assert len(job_queue.jobs[job_name]) >= len(original_jobs) + 1
    assert any(job.removed for job in original_jobs)


def test_delete_cancels_job_and_removes_record(calendar_path) -> None:
    now = datetime.now(tz=calendar_store.BOT_TZ)
    reminder = asyncio_run(
        calendar_store.add_reminder(
            trigger_at=now + timedelta(hours=3),
            text="Follow up",
            chat_id=1,
            user_id=1,
        )
    )
    job_queue = DummyJobQueue()
    application = SimpleNamespace(job_queue=job_queue, bot=SimpleNamespace(), bot_data={})
    scheduler = OneShotReminderScheduler(application=application)
    application.bot_data["reminder_scheduler"] = scheduler
    context = SimpleNamespace(application=application)

    asyncio_run(scheduler.schedule_reminder(reminder))
    job_name = scheduler._job_name(reminder.id)
    assert job_queue.get_jobs_by_name(job_name)

    result = asyncio_run(
        handlers._handle_reminder_delete(  # type: ignore[attr-defined]
            context,
            reminder_id=reminder.id,
            user_id=reminder.user_id,
            chat_id=reminder.chat_id,
        )
    )
    assert result.status == "ok"
    assert asyncio_run(calendar_store.get_reminder(reminder.id)) is None
    assert job_queue.get_jobs_by_name(job_name)[0].removed is True

    # second click => refused, not crash
    result2 = asyncio_run(
        handlers._handle_reminder_delete(  # type: ignore[attr-defined]
            context,
            reminder_id=reminder.id,
            user_id=reminder.user_id,
            chat_id=reminder.chat_id,
        )
    )
    assert result2.status == "refused"


def test_security_static_callback_rejects_tampering() -> None:
    assert handlers._parse_static_callback("cb:rem:s:xx:deadbeef") is None  # type: ignore[attr-defined]
    assert handlers._parse_static_callback("cb:rem:s:5:") is None  # type: ignore[attr-defined]
    assert handlers._parse_static_callback("cb:rem:unknown:5:deadbeef") is None  # type: ignore[attr-defined]

