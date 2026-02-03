from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta

from telegram.ext import Application, ContextTypes

from app.core import calendar_store
from app.infra.messaging import safe_send_bot_text

LOGGER = logging.getLogger(__name__)


def _get_tick_seconds() -> int:
    try:
        value = int(os.getenv("REMINDER_TICK_SECONDS", "20"))
    except ValueError:
        return 20
    return max(1, value)


def _get_grace_minutes() -> int:
    try:
        value = int(os.getenv("REMINDER_GRACE_MINUTES", "60"))
    except ValueError:
        return 60
    return max(1, value)


async def _process_due_reminders(application: Application) -> None:
    now = datetime.now(tz=calendar_store.VIENNA_TZ)
    grace_window = timedelta(minutes=_get_grace_minutes())
    due_items = await calendar_store.list_due_reminders(now)
    for item in due_items:
        if not item.chat_id:
            LOGGER.warning(
                "Reminder skipped (missing chat_id): event_id=%s user_id=%s chat_id=%s remind_at=%s",
                item.id,
                item.user_id,
                item.chat_id,
                item.remind_at.isoformat(),
            )
            await calendar_store.mark_reminder_sent(item.id, now, missed=True)
            continue
        if now - item.remind_at > grace_window:
            LOGGER.warning(
                "Reminder missed: event_id=%s user_id=%s chat_id=%s remind_at=%s",
                item.id,
                item.user_id,
                item.chat_id,
                item.remind_at.isoformat(),
            )
            await calendar_store.mark_reminder_sent(item.id, now, missed=True)
            continue
        message_time = item.dt.astimezone(calendar_store.VIENNA_TZ).strftime("%H:%M, %Y-%m-%d")
        text = f"⏰ Напоминание: {item.title} ({message_time})"
        try:
            await safe_send_bot_text(application.bot, item.chat_id, text)
        except Exception:
            LOGGER.exception(
                "Reminder send failed: event_id=%s user_id=%s chat_id=%s remind_at=%s",
                item.id,
                item.user_id,
                item.chat_id,
                item.remind_at.isoformat(),
            )
            continue
        LOGGER.info(
            "Reminder sent: event_id=%s user_id=%s chat_id=%s remind_at=%s request_id=%s",
            item.id,
            item.user_id,
            item.chat_id,
            item.remind_at.isoformat(),
            "-",
        )
        await calendar_store.mark_reminder_sent(item.id, now, missed=False)


async def _job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _process_due_reminders(context.application)


async def _loop_runner(application: Application) -> None:
    tick_seconds = _get_tick_seconds()
    try:
        while True:
            await _process_due_reminders(application)
            await asyncio.sleep(tick_seconds)
    except asyncio.CancelledError:
        LOGGER.info("Reminder scheduler task cancelled")
        raise


async def post_init(application: Application) -> None:
    tick_seconds = _get_tick_seconds()
    if application.job_queue:
        job = application.job_queue.run_repeating(_job_callback, interval=tick_seconds, first=0)
        application.bot_data["reminder_job"] = job
        LOGGER.info("Reminder scheduler started via job_queue (tick=%s)", tick_seconds)
        return
    task = asyncio.create_task(_loop_runner(application), name="reminder-scheduler")
    application.bot_data["reminder_task"] = task
    LOGGER.info("Reminder scheduler started via background task (tick=%s)", tick_seconds)


async def post_shutdown(application: Application) -> None:
    task = application.bot_data.pop("reminder_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        LOGGER.info("Reminder scheduler task shutdown complete")
