from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from app.bot.actions import ActionStore, build_inline_keyboard
from app.core.result import Action

from app.core import calendar_store

if TYPE_CHECKING:
    from app.core.app_scheduler import AppScheduler

LOGGER = logging.getLogger(__name__)


def _get_default_offset_minutes() -> int:
    try:
        value = int(os.getenv("REMINDER_DEFAULT_OFFSET_MINUTES", "10"))
    except ValueError:
        return 10
    return max(0, value)


def _get_max_future_days() -> int:
    try:
        value = int(os.getenv("REMINDER_MAX_FUTURE_DAYS", "365"))
    except ValueError:
        return 365
    return max(1, value)


class ReminderScheduler:
    def __init__(
        self,
        application: Any,
        calendar_store_module=calendar_store,
        timezone: ZoneInfo = calendar_store.BOT_TZ,
        max_future_days: int | None = None,
        app_scheduler: AppScheduler | None = None,
    ) -> None:
        self._application = application
        self._store = calendar_store_module
        self._timezone = timezone
        self._max_future_days = max_future_days or _get_max_future_days()
        self._app_scheduler = app_scheduler

    async def schedule_reminder(
        self,
        reminder: calendar_store.ReminderItem,
        *,
        now: datetime | None = None,
    ) -> str | None:
        if not reminder.enabled:
            LOGGER.info("Reminder skipped (disabled): reminder_id=%s", reminder.id)
            return None
        current = now or datetime.now(tz=self._timezone)
        if current.tzinfo is None:
            current = current.replace(tzinfo=self._timezone)
        else:
            current = current.astimezone(self._timezone)
        trigger_at = reminder.trigger_at
        if trigger_at.tzinfo is None:
            trigger_at = trigger_at.replace(tzinfo=self._timezone)
        else:
            trigger_at = trigger_at.astimezone(self._timezone)
        if trigger_at <= current:
            LOGGER.info(
                "Reminder past trigger scheduled immediately: reminder_id=%s trigger_at=%s",
                reminder.id,
                trigger_at.isoformat(),
            )
            trigger_at = current
        if trigger_at > current + timedelta(days=self._max_future_days):
            LOGGER.info(
                "Reminder skipped (too far): reminder_id=%s trigger_at=%s",
                reminder.id,
                trigger_at.isoformat(),
            )
            return None

        if self._app_scheduler is None:
            LOGGER.warning("Reminder scheduling skipped: no app_scheduler (reminder_id=%s)", reminder.id)
            return None
        if self._app_scheduler.add_reminder_job(reminder.id, trigger_at):
            LOGGER.info(
                "Reminder scheduled: reminder_id=%s event_id=%s trigger_at=%s",
                reminder.id,
                reminder.event_id,
                reminder.trigger_at.isoformat(),
            )
            return self._job_name(reminder.id)
        return None

    async def cancel_reminder(self, reminder_id: str) -> bool:
        removed = False
        if self._app_scheduler is not None:
            removed = self._app_scheduler.remove_reminder_job(reminder_id)
        store_updated = await self._store.disable_reminder(reminder_id)
        LOGGER.info(
            "Reminder canceled: reminder_id=%s job_removed=%s store_updated=%s",
            reminder_id,
            removed,
            store_updated,
        )
        return removed or store_updated

    async def restore_all(self, now: datetime | None = None) -> int:
        current = now or datetime.now(tz=self._timezone)
        if current.tzinfo is None:
            current = current.replace(tzinfo=self._timezone)
        else:
            current = current.astimezone(self._timezone)
        reminders = await self._store.list_reminders(current, limit=None, include_disabled=True)
        restored = 0
        for reminder in reminders:
            if not reminder.enabled:
                continue
            if reminder.sent_at is not None and reminder.recurrence is None:
                continue
            trigger_at = reminder.trigger_at
            if trigger_at.tzinfo is None:
                trigger_at = trigger_at.replace(tzinfo=current.tzinfo)
            else:
                trigger_at = trigger_at.astimezone(current.tzinfo)
            if trigger_at <= current:
                continue
            if trigger_at > current + timedelta(days=self._max_future_days):
                continue
            if await self.schedule_reminder(reminder, now=current):
                restored += 1
        LOGGER.info("Reminder restore complete: restored=%s total=%s", restored, len(reminders))
        return restored

    async def schedule_for_event(
        self,
        event: calendar_store.CalendarItem,
        trigger_at: datetime | None = None,
        enabled: bool = True,
    ) -> calendar_store.ReminderItem:
        reminder_time = trigger_at or event.dt
        reminder = await self._store.ensure_reminder_for_event(
            event=event,
            trigger_at=reminder_time,
            enabled=enabled,
        )
        await self.schedule_reminder(reminder)
        return reminder

    @staticmethod
    def _job_name(reminder_id: str) -> str:
        return f"reminder:{reminder_id}"


def build_reminder_followup_actions(reminder: calendar_store.ReminderItem) -> list[Action]:
    """Follow-ups после отправки напоминания: макс. 3 кнопки — Детали, Отложить, Удалить."""
    base_trigger = reminder.trigger_at.isoformat()
    return [
        Action(
            id=f"reminder_details:{reminder.id}",
            label="📌 Детали",
            payload={"op": "reminder_details", "reminder_id": reminder.id},
        ),
        Action(
            id=f"reminder_snooze_menu:{reminder.id}",
            label="⏸ Отложить",
            payload={"op": "reminder_snooze_menu", "reminder_id": reminder.id, "base_trigger_at": base_trigger},
        ),
        Action(
            id="utility_reminders.delete",
            label="🗑 Удалить",
            payload={"op": "reminder.delete_confirm", "reminder_id": reminder.id},
        ),
    ]


def _build_reminder_actions(reminder: calendar_store.ReminderItem) -> list[Action]:
    return build_reminder_followup_actions(reminder)


def get_default_offset_minutes() -> int:
    return _get_default_offset_minutes()
