"""Планировщик на APScheduler: напоминания и дайджест по TZ пользователя. Без PTB/JobQueue."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.core import calendar_store
from app.core.user_profile import DEFAULT_TIMEZONE

LOGGER = logging.getLogger(__name__)

DIGEST_HOUR = 9
DIGEST_MINUTE = 0


def _job_name_reminder(reminder_id: str) -> str:
    return f"reminder:{reminder_id}"


def _job_name_digest(user_id: int) -> str:
    return f"digest:{user_id}"


class AppScheduler:
    """Единый планировщик на APScheduler: timezone на уровне trigger."""

    def __init__(
        self,
        *,
        application: Any,
        calendar_store_module: Any = calendar_store,
        profile_store: Any = None,
    ) -> None:
        self._scheduler = AsyncIOScheduler()
        self._application = application
        self._store = calendar_store_module
        self._profile_store = profile_store

    def start(self) -> None:
        if self._scheduler.running:
            LOGGER.info("AppScheduler already started, skipping")
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        self._scheduler._eventloop = loop
        self._scheduler.start()
        LOGGER.info("AppScheduler (APScheduler) started")

    def shutdown(self, wait: bool = True) -> None:
        if not self._scheduler.running:
            return
        try:
            self._scheduler.shutdown(wait=wait)
            LOGGER.info("AppScheduler shutdown")
        except Exception:
            LOGGER.exception("AppScheduler shutdown error")

    def add_reminder_job(
        self,
        reminder_id: str,
        trigger_at: datetime,
    ) -> bool:
        """Поставить одноразовый job на отправку напоминания в trigger_at (timezone-aware)."""
        job_id = _job_name_reminder(reminder_id)
        self.remove_job(job_id)
        try:
            self._scheduler.add_job(
                _run_reminder_job,
                trigger=DateTrigger(run_date=trigger_at),
                id=job_id,
                replace_existing=True,
                kwargs={
                    "reminder_id": reminder_id,
                    "application": self._application,
                    "calendar_store_module": self._store,
                    "profile_store": self._profile_store,
                },
            )
            LOGGER.info(
                "Reminder job scheduled: reminder_id=%s trigger_at=%s",
                reminder_id,
                trigger_at.isoformat(),
            )
            return True
        except Exception:
            LOGGER.exception("Failed to add reminder job: reminder_id=%s", reminder_id)
            return False

    def remove_reminder_job(self, reminder_id: str) -> bool:
        job_id = _job_name_reminder(reminder_id)
        return self.remove_job(job_id)

    def remove_job(self, job_id: str) -> bool:
        try:
            self._scheduler.remove_job(job_id)
            return True
        except Exception:
            return False

    def add_digest_job(self, user_id: int, chat_id: int, timezone_str: str) -> bool:
        """Поставить ежедневный дайджест в 09:00 по времени пользователя (timezone на trigger)."""
        job_id = _job_name_digest(user_id)
        self.remove_job(job_id)
        try:
            tz = ZoneInfo(timezone_str)
            self._scheduler.add_job(
                _run_digest_job,
                trigger=CronTrigger(hour=DIGEST_HOUR, minute=DIGEST_MINUTE, timezone=tz),
                id=job_id,
                replace_existing=True,
                kwargs={
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "application": self._application,
                },
            )
            LOGGER.info(
                "digest_scheduled user_id=%s chat_id=%s timezone=%s",
                user_id,
                chat_id,
                timezone_str,
            )
            return True
        except Exception:
            LOGGER.exception("Failed to add digest job: user_id=%s", user_id)
            return False

    def remove_digest_job(self, user_id: int) -> bool:
        return self.remove_job(_job_name_digest(user_id))

    def get_jobs_by_name(self, name: str) -> list[Any]:
        """Для совместимости с тестами: вернуть job'ы, id которых начинается с name."""
        jobs = self._scheduler.get_jobs()
        return [j for j in jobs if j.id == name or (name and j.id.startswith(name + ":"))]


async def _run_reminder_job(
    reminder_id: str,
    application: Any,
    calendar_store_module: Any,
    profile_store: Any = None,
) -> None:
    """Callback APScheduler: отправить напоминание, обновить store, при необходимости перепланировать."""
    from app.bot.actions import ActionStore, build_inline_keyboard
    from app.core.reminders import _build_reminder_actions

    reminder = await calendar_store_module.get_reminder(reminder_id)
    if reminder is None:
        LOGGER.warning("Reminder not found: reminder_id=%s", reminder_id)
        return
    if not reminder.enabled:
        LOGGER.info("Reminder disabled before send: reminder_id=%s", reminder_id)
        return

    tz = calendar_store_module.BOT_TZ
    if profile_store is not None:
        try:
            profile = profile_store.get(reminder.user_id)
            if profile and getattr(profile, "timezone", None):
                tz = ZoneInfo(profile.timezone)
        except Exception:
            pass

    event = await calendar_store_module.get_event(reminder.event_id)
    event_dt = event.dt if event else reminder.trigger_at
    event_label = event_dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    text = f"⏰ Напоминание: {reminder.text}\nКогда: {event_label}"

    actions = _build_reminder_actions(reminder)
    reply_markup = None
    action_store = getattr(application, "bot_data", {}).get("action_store")
    if isinstance(action_store, ActionStore):
        reply_markup = build_inline_keyboard(
            actions,
            store=action_store,
            user_id=reminder.user_id,
            chat_id=reminder.chat_id,
            columns=2,
        )
    try:
        bot = getattr(application, "bot", None)
        if bot is not None:
            await bot.send_message(
                chat_id=reminder.chat_id,
                text=text,
                reply_markup=reply_markup,
            )
            if reply_markup is not None:
                LOGGER.info(
                    "reminder_followup_shown reminder_id=%s user_id=%s chat_id=%s",
                    reminder.id,
                    reminder.user_id,
                    reminder.chat_id,
                )
    except Exception:
        LOGGER.exception(
            "Reminder send failed: reminder_id=%s chat_id=%s",
            reminder.id,
            reminder.chat_id,
        )
        return

    fired_at = datetime.now(tz=tz)
    next_reminder = await calendar_store_module.mark_reminder_sent(
        reminder.id, fired_at, missed=False
    )

    if next_reminder is not None:
        scheduler = getattr(application, "bot_data", {}).get("reminder_scheduler")
        if scheduler is not None and hasattr(scheduler, "schedule_reminder"):
            await scheduler.schedule_reminder(next_reminder)
            LOGGER.info(
                "Reminder recurrence scheduled: reminder_id=%s next_trigger_at=%s",
                reminder.id,
                next_reminder.trigger_at.isoformat(),
            )

    LOGGER.info(
        "Reminder sent: reminder_id=%s user_id=%s chat_id=%s trigger_at=%s",
        reminder.id,
        reminder.user_id,
        reminder.chat_id,
        reminder.trigger_at.isoformat(),
    )


async def _run_digest_job(user_id: int, chat_id: int, application: Any) -> None:
    """Callback APScheduler: отправить один дайджест в день пользователю."""
    try:
        bot = getattr(application, "bot", None)
        if bot is None:
            return
        await bot.send_message(
            chat_id=chat_id,
            text="☀️ Доброе утро! Дайджест за день: используйте /reminders и /calendar для просмотра.",
        )
        LOGGER.info("Digest sent: user_id=%s chat_id=%s", user_id, chat_id)
    except Exception:
        LOGGER.exception("Digest send failed: user_id=%s chat_id=%s", user_id, chat_id)
