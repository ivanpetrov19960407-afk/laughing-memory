"""Daily digest: once per day send a summary of today's reminders to users who have digest enabled."""

from __future__ import annotations

import logging
from datetime import datetime

from telegram.ext import ContextTypes

from app.core import calendar_store
from app.infra.messaging import safe_send_bot_text

LOGGER = logging.getLogger(__name__)


async def run_daily_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run once per day: for each user with digest_enabled, send today's reminders (if any)."""
    application = context.application
    profile_store = application.bot_data.get("profile_store")
    if not profile_store:
        LOGGER.warning("Daily digest skipped: profile_store not in bot_data")
        return
    bot = application.bot
    tz = calendar_store.BOT_TZ
    today = datetime.now(tz=tz).date()
    today_str = today.isoformat()
    for user_id in profile_store.get_all_user_ids():
        try:
            profile = profile_store.get(user_id)
            if not profile.digest_enabled or profile.digest_chat_id is None:
                continue
            if profile.last_digest_sent_date == today_str:
                continue
            chat_id = profile.digest_chat_id
            items = await calendar_store.list_reminders_for_day(user_id, chat_id, today, tz=tz)
            if not items:
                continue
            lines = ["📬 Дайджест на сегодня:"]
            for item in items:
                when_label = item.trigger_at.astimezone(tz).strftime("%H:%M")
                lines.append(f"• {when_label} — {item.text}")
            text = "\n".join(lines)
            await safe_send_bot_text(bot, chat_id, text)
            profile_store.update(user_id, {"last_digest_sent_date": today_str})
            LOGGER.info("Daily digest sent: user_id=%s chat_id=%s count=%s", user_id, chat_id, len(items))
        except Exception:
            LOGGER.exception("Daily digest failed for user_id=%s", user_id)
