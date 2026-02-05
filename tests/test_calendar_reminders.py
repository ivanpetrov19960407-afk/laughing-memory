from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core import calendar_store
from app.core.reminders import ReminderScheduler


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


def _build_application(job_queue: DummyJobQueue):
    return SimpleNamespace(job_queue=job_queue, bot=SimpleNamespace())


def _write_store(store: dict[str, object]) -> None:
    calendar_store.save_store_atomic(store)


def test_restore_schedules_only_future(calendar_path) -> None:
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.MOSCOW_TZ)
    past_trigger = (now - timedelta(minutes=30)).isoformat()
    future_trigger = (now + timedelta(minutes=30)).isoformat()
    store = {
        "events": [
            {
                "event_id": "event1",
                "dt_start": now.isoformat(),
                "text": "Past",
                "created_at": now.isoformat(),
                "chat_id": 1,
                "user_id": 1,
            },
            {
                "event_id": "event2",
                "dt_start": now.isoformat(),
                "text": "Future",
                "created_at": now.isoformat(),
                "chat_id": 1,
                "user_id": 1,
            },
        ],
        "reminders": [
            {
                "reminder_id": "rem1",
                "event_id": "event1",
                "user_id": 1,
                "chat_id": 1,
                "trigger_at": past_trigger,
                "text": "Past",
                "enabled": True,
                "sent_at": None,
            },
            {
                "reminder_id": "rem2",
                "event_id": "event2",
                "user_id": 1,
                "chat_id": 1,
                "trigger_at": future_trigger,
                "text": "Future",
                "enabled": True,
                "sent_at": None,
            },
        ],
        "updated_at": now.isoformat(),
    }
    _write_store(store)
    job_queue = DummyJobQueue()
    application = _build_application(job_queue)
    scheduler = ReminderScheduler(application=application)
    restored = asyncio_run(scheduler.restore_all(now))
    assert restored == 1
    assert scheduler._job_name("rem2") in job_queue.jobs
    assert scheduler._job_name("rem1") not in job_queue.jobs


def test_add_creates_reminder(calendar_path) -> None:
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.MOSCOW_TZ)
    item = asyncio_run(
        calendar_store.add_item(
            dt=now + timedelta(hours=1),
            title="Meeting",
            chat_id=1,
            remind_at=now + timedelta(minutes=30),
            user_id=1,
        )
    )
    reminder_id = item["reminder"]["reminder_id"]
    store = calendar_store.load_store()
    reminders = store.get("reminders") or []
    assert any(rem.get("reminder_id") == reminder_id and rem.get("enabled") is True for rem in reminders)


def test_del_cancels_reminder(calendar_path) -> None:
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.MOSCOW_TZ)
    result_item = asyncio_run(
        calendar_store.add_item(
            dt=now + timedelta(hours=2),
            title="Dentist",
            chat_id=1,
            remind_at=now + timedelta(hours=1, minutes=45),
            user_id=1,
        )
    )
    reminder_id = result_item["reminder"]["reminder_id"]
    job_queue = DummyJobQueue()
    application = _build_application(job_queue)
    scheduler = ReminderScheduler(application=application)
    reminder_item = asyncio_run(calendar_store.get_reminder(reminder_id))
    asyncio_run(scheduler.schedule_reminder(reminder_item))
    removed, reminder_to_cancel = asyncio_run(calendar_store.delete_item(result_item["event"]["event_id"]))
    assert removed is True
    assert reminder_to_cancel == reminder_id
    asyncio_run(scheduler.cancel_reminder(reminder_id))
    assert job_queue.get_jobs_by_name(scheduler._job_name(reminder_id))[0].removed is True
    store = calendar_store.load_store()
    assert all(rem.get("reminder_id") != reminder_id for rem in store.get("reminders") or [])


def test_snooze_shifts_trigger(calendar_path) -> None:
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.MOSCOW_TZ)
    item = asyncio_run(
        calendar_store.add_item(
            dt=now + timedelta(hours=2),
            title="Standup",
            chat_id=1,
            remind_at=now + timedelta(minutes=15),
            user_id=1,
        )
    )
    reminder_id = item["reminder"]["reminder_id"]
    reminder = asyncio_run(calendar_store.get_reminder(reminder_id))
    updated = asyncio_run(
        calendar_store.apply_snooze(reminder_id, minutes=30, now=now, base_trigger_at=reminder.trigger_at)
    )
    assert updated is not None
    assert updated.trigger_at == reminder.trigger_at + timedelta(minutes=30)


def test_recurrence_creates_next_trigger(calendar_path) -> None:
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.MOSCOW_TZ)
    store = {
        "events": [
            {
                "event_id": "event1",
                "dt_start": now.isoformat(),
                "text": "Daily",
                "created_at": now.isoformat(),
                "chat_id": 1,
                "user_id": 1,
            }
        ],
        "reminders": [
            {
                "reminder_id": "rem1",
                "event_id": "event1",
                "user_id": 1,
                "chat_id": 1,
                "trigger_at": now.isoformat(),
                "text": "Daily",
                "enabled": True,
                "sent_at": None,
                "status": "active",
                "recurrence": {"freq": "daily"},
                "last_triggered_at": None,
            }
        ],
        "updated_at": now.isoformat(),
    }
    _write_store(store)
    next_item = asyncio_run(calendar_store.mark_reminder_sent("rem1", now, missed=False))
    assert next_item is not None
    expected = now + timedelta(days=1)
    assert next_item.trigger_at == expected


def test_disable_reminder_updates_status(calendar_path) -> None:
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.MOSCOW_TZ)
    item = asyncio_run(
        calendar_store.add_item(
            dt=now + timedelta(hours=1),
            title="Cleanup",
            chat_id=1,
            remind_at=now + timedelta(minutes=20),
            user_id=1,
        )
    )
    reminder_id = item["reminder"]["reminder_id"]
    updated = asyncio_run(calendar_store.disable_reminder(reminder_id))
    assert updated is True
    reminder = asyncio_run(calendar_store.get_reminder(reminder_id))
    assert reminder is not None
    assert reminder.status == "disabled"


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def test_scheduler_cancel_disables_reminder(calendar_path) -> None:
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.MOSCOW_TZ)
    item = asyncio_run(
        calendar_store.add_item(
            dt=now + timedelta(hours=1),
            title="Cleanup",
            chat_id=1,
            remind_at=now + timedelta(minutes=20),
            user_id=1,
        )
    )
    reminder_id = item["reminder"]["reminder_id"]
    job_queue = DummyJobQueue()
    scheduler = ReminderScheduler(application=_build_application(job_queue))
    reminder = asyncio_run(calendar_store.get_reminder(reminder_id))
    asyncio_run(scheduler.schedule_reminder(reminder))
    asyncio_run(scheduler.cancel_reminder(reminder_id))
    loaded = asyncio_run(calendar_store.get_reminder(reminder_id))
    assert loaded is not None
    assert loaded.enabled is False
