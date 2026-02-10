from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core import calendar_store
from app.core.daily_digest import DIGEST_TZ, collect_daily_digest, render_daily_digest
from app.infra.messaging import safe_send_bot_text

LOGGER = logging.getLogger(__name__)

JOB_ID = "daily_digest"


async def _send_digests_for_enabled_users(application, *, now: datetime | None = None, tz: ZoneInfo = DIGEST_TZ) -> int:
    profile_store = application.bot_data.get("profile_store")
    if profile_store is None:
        LOGGER.warning("Daily digest skipped: profile_store missing")
        return 0
    current = now or datetime.now(tz=tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    sent = 0
    for user_id in profile_store.list_user_ids():
        try:
            profile = profile_store.get(user_id)
        except Exception:
            LOGGER.exception("Daily digest skipped: failed to load profile user_id=%s", user_id)
            continue
        if not bool(getattr(profile, "daily_digest_enabled", False)):
            continue
        date_key = current.astimezone(tz).date().isoformat()
        last_sent = getattr(profile, "daily_digest_last_sent_date", None)
        if isinstance(last_sent, str) and last_sent == date_key:
            continue
        chat_id = await calendar_store.resolve_user_chat_id(user_id)
        if not isinstance(chat_id, int) or chat_id == 0:
            LOGGER.info("Daily digest skipped: chat_id unknown user_id=%s", user_id)
            continue
        data = await collect_daily_digest(user_id=user_id, now=current, tz=tz)
        text = render_daily_digest(data, tz=tz)
        if not text:
            continue
        try:
            await safe_send_bot_text(application.bot, chat_id, text)
        except Exception:
            LOGGER.exception("Daily digest send failed: user_id=%s chat_id=%s", user_id, chat_id)
            continue
        profile_store.update(user_id, {"daily_digest_last_sent_date": date_key})
        sent += 1
        LOGGER.info("Daily digest sent: user_id=%s chat_id=%s date=%s", user_id, chat_id, date_key)
    return sent


def _run_digest_job(application) -> None:
    # APScheduler runs callables; we schedule the coroutine on the loop.
    asyncio.create_task(_send_digests_for_enabled_users(application), name="daily-digest")


def start_digest_scheduler(application, *, tz: ZoneInfo = DIGEST_TZ) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=tz)
    trigger = CronTrigger(hour=9, minute=0, timezone=tz)
    scheduler.add_job(_run_digest_job, trigger=trigger, id=JOB_ID, replace_existing=True, args=[application])
    scheduler.start()
    LOGGER.info("Daily digest scheduler started: tz=%s job_id=%s", tz.key if isinstance(tz, ZoneInfo) else str(tz), JOB_ID)
    return scheduler


def stop_digest_scheduler(scheduler: AsyncIOScheduler | None) -> None:
    if scheduler is None:
        return
    try:
        scheduler.shutdown(wait=False)
    except Exception:
        LOGGER.exception("Failed to shutdown digest scheduler")

