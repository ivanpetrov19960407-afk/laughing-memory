from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram.ext import Application, ContextTypes

from app.bot.actions import ActionStore, build_inline_keyboard
from app.core.result import Action

from app.core import calendar_store

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
        application: Application,
        calendar_store_module=calendar_store,
        timezone: ZoneInfo = calendar_store.BOT_TZ,
        max_future_days: int | None = None,
    ) -> None:
        self._application = application
        self._store = calendar_store_module
        self._timezone = timezone
        self._max_future_days = max_future_days or _get_max_future_days()

    async def schedule_reminder(
        self,
        reminder: calendar_store.ReminderItem,
        *,
        now: datetime | None = None,
    ) -> str | None:
        if not self._application.job_queue:
            LOGGER.warning("Reminder scheduling skipped: job_queue unavailable (reminder_id=%s)", reminder.id)
            return None
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
        when_value: datetime | int = reminder.trigger_at
        if trigger_at <= current:
            LOGGER.info(
                "Reminder past trigger scheduled immediately: reminder_id=%s trigger_at=%s",
                reminder.id,
                trigger_at.isoformat(),
            )
            when_value = 0
        if trigger_at > current + timedelta(days=self._max_future_days):
            LOGGER.info(
                "Reminder skipped (too far): reminder_id=%s trigger_at=%s",
                reminder.id,
                trigger_at.isoformat(),
            )
            return None
        job_name = self._job_name(reminder.id)
        for job in self._application.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()
        self._application.job_queue.run_once(
            self._job_callback,
            when=when_value,
            name=job_name,
            data={"reminder_id": reminder.id},
        )
        LOGGER.info('Added job "%s" to reminder scheduler', job_name)
        LOGGER.info(
            "Reminder scheduled: reminder_id=%s event_id=%s trigger_at=%s",
            reminder.id,
            reminder.event_id,
            reminder.trigger_at.isoformat(),
        )
        return job_name

    async def cancel_reminder(self, reminder_id: str) -> bool:
        removed = False
        if self._application.job_queue:
            for job in self._application.job_queue.get_jobs_by_name(self._job_name(reminder_id)):
                job.schedule_removal()
                removed = True
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

    def _user_zone(self, user_id: int) -> ZoneInfo:
        """Resolve user timezone from profile if available."""
        store = getattr(self._application.bot_data or {}, "get", lambda _: None)("profile_store")
        if store is None or not hasattr(store, "get"):
            return self._timezone
        try:
            profile = store.get(user_id)
            tz_str = getattr(profile, "timezone", None) if profile else None
            if isinstance(tz_str, str) and tz_str.strip():
                return ZoneInfo(tz_str.strip())
        except Exception:
            LOGGER.debug("User timezone fallback to default: user_id=%s", user_id)
        return self._timezone

    async def _job_callback(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        reminder_id = None
        if context.job and isinstance(context.job.data, dict):
            reminder_id = context.job.data.get("reminder_id")
        if not reminder_id or not isinstance(reminder_id, str):
            LOGGER.warning("Reminder job missing reminder_id")
            return
        reminder = await self._store.get_reminder(reminder_id)
        if reminder is None:
            LOGGER.warning("Reminder not found: reminder_id=%s", reminder_id)
            return
        if not reminder.enabled:
            LOGGER.info("Reminder disabled before send: reminder_id=%s", reminder_id)
            return
        event = await self._store.get_event(reminder.event_id)
        event_dt = event.dt if event else reminder.trigger_at
        user_tz = self._user_zone(reminder.user_id)
        event_label = event_dt.astimezone(user_tz).strftime("%Y-%m-%d %H:%M")
        text = f"⏰ Напоминание: {reminder.text}\nКогда: {event_label}"
        actions = _build_reminder_actions(reminder)
        action_store = self._application.bot_data.get("action_store")
        reply_markup = None
        if isinstance(action_store, ActionStore):
            reply_markup = build_inline_keyboard(
                actions,
                store=action_store,
                user_id=reminder.user_id,
                chat_id=reminder.chat_id,
                columns=2,
            )
        try:
            await self._application.bot.send_message(chat_id=reminder.chat_id, text=text, reply_markup=reply_markup)
        except Exception:
            LOGGER.exception(
                "Reminder send failed: reminder_id=%s event_id=%s chat_id=%s trigger_at=%s",
                reminder.id,
                reminder.event_id,
                reminder.chat_id,
                reminder.trigger_at.isoformat(),
            )
            return
        fired_at = datetime.now(tz=self._timezone)
        next_reminder = await self._store.mark_reminder_sent(reminder.id, fired_at, missed=False)
        if next_reminder is not None:
            await self.schedule_reminder(next_reminder)
            LOGGER.info(
                "Reminder recurrence scheduled: reminder_id=%s next_trigger_at=%s",
                reminder.id,
                next_reminder.trigger_at.isoformat(),
            )
        LOGGER.info(
            "Reminder sent: reminder_id=%s event_id=%s chat_id=%s trigger_at=%s",
            reminder.id,
            reminder.event_id,
            reminder.chat_id,
            reminder.trigger_at.isoformat(),
        )

    @staticmethod
    def _job_name(reminder_id: str) -> str:
        return f"reminder:{reminder_id}"


def _build_reminder_actions(reminder: calendar_store.ReminderItem) -> list[Action]:
    """Build up to 3 contextual follow-up actions for a sent reminder."""
    base_trigger = reminder.trigger_at.isoformat()
    return [
        Action(
            id=f"reminder_show_details:{reminder.id}",
            label="📌 Показать детали",
            payload={"op": "reminder_show_details", "reminder_id": reminder.id},
        ),
        Action(
            id=f"reminder_repeat_menu:{reminder.id}",
            label="🔁 Повторить через…",
            payload={"op": "reminder_repeat_menu", "reminder_id": reminder.id, "base_trigger_at": base_trigger},
        ),
        Action(
            id=f"reminder_snooze_menu:{reminder.id}",
            label="🕒 Отложить на…",
            payload={"op": "reminder_snooze_menu", "reminder_id": reminder.id, "base_trigger_at": base_trigger},
        ),
    ]


def get_default_offset_minutes() -> int:
    return _get_default_offset_minutes()
