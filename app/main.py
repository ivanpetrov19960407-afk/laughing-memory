from __future__ import annotations

import asyncio
import logging
import time
import warnings
from collections import defaultdict, deque

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from telegram.warnings import PTBUserWarning

from app.bot import actions, handlers, wizard
from app.core.orchestrator import Orchestrator, load_orchestrator_config
from app.core.reminders import ReminderScheduler
from app.core.dialog_memory import DialogMemory
from app.core.wizard_manager import WizardManager
from app.core.wizard_runtime import WizardRuntime
from app.core.wizards import calendar_add as wizard_calendar_add
from app.core.wizards import echo as wizard_echo
from app.core.wizards import echo_confirm as wizard_echo_confirm
from app.infra.access import AccessController
from app.infra.allowlist import AllowlistStore, extract_allowed_user_ids
from app.infra.config import load_settings
from app.infra.llm import OpenAIClient, PerplexityClient
from app.infra.rate_limit import RateLimiter as LLMRateLimiter
from app.infra.rate_limiter import RateLimiter
from app.infra.storage import TaskStorage
from app.storage.wizard_store import WizardStore


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",

    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)
    logging.getLogger("httpx").disabled = True
    logging.getLogger("httpcore").disabled = True
    logging.getLogger("httpx").propagate = False
    logging.getLogger("httpcore").propagate = False





    settings = load_settings()
    config = load_orchestrator_config(settings.orchestrator_config_path)
    if settings.facts_only_default is not None:
        config["facts_only_default"] = settings.facts_only_default
    storage = TaskStorage(settings.db_path)
    llm_client = None
    openai_client = None
    if settings.openai_api_key:
        openai_client = OpenAIClient(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            image_model=settings.openai_image_model,
            timeout_seconds=settings.openai_timeout_seconds,
        )
        llm_client = openai_client
    elif settings.perplexity_api_key:
        llm_client = PerplexityClient(
            api_key=settings.perplexity_api_key,
            base_url=settings.perplexity_base_url,
            timeout_seconds=settings.perplexity_timeout_seconds,
        )
    config_allowlist_ids = extract_allowed_user_ids(config)
    initial_allowlist_ids = settings.allowed_user_ids or config_allowlist_ids
    allowlist_store = AllowlistStore(
        path=settings.allowlist_path,
        initial_user_ids=initial_allowlist_ids,
    )
    asyncio.run(allowlist_store.load())
    admin_user_ids = settings.admin_user_ids or settings.allowed_user_ids or config_allowlist_ids
    access = AccessController(allowlist=allowlist_store, admin_user_ids=admin_user_ids)

    rate_limits = config.get("rate_limits", {}).get("llm", {})
    per_minute = settings.llm_per_minute
    if per_minute is None:
        per_minute = rate_limits.get("per_minute")
    per_day = settings.llm_per_day
    if per_day is None:
        per_day = rate_limits.get("per_day")
    rate_limiter = LLMRateLimiter(per_minute=per_minute, per_day=per_day)

    orchestrator = Orchestrator(
        config=config,
        storage=storage,
        llm_client=llm_client,
        access=access,
        rate_limiter=rate_limiter,
        llm_history_turns=settings.llm_history_turns,
        llm_model=settings.openai_model if openai_client else settings.perplexity_model,
    )
    dialog_memory = DialogMemory(
        settings.dialog_memory_path,
        max_turns=settings.context_max_turns,
    )
    asyncio.run(dialog_memory.load())

    warnings.filterwarnings("ignore", message="No JobQueue set up", category=PTBUserWarning)
    application = Application.builder().token(settings.bot_token).build()
    wizard_manager = WizardManager(timeout_seconds=900)
    wizard_runtime = WizardRuntime(wizard_manager)
    wizard_calendar_add.register(wizard_runtime)
    wizard_echo.register(wizard_runtime)
    wizard_echo_confirm.register(wizard_runtime)
    application.bot_data["wizard_runtime"] = wizard_runtime
    reminder_scheduler = ReminderScheduler(
        application=application,
        max_future_days=settings.reminder_max_future_days,
    )
    application.bot_data["reminder_scheduler"] = reminder_scheduler
    application.bot_data["orchestrator"] = orchestrator
    application.bot_data["storage"] = storage
    application.bot_data["allowlist_store"] = allowlist_store
    application.bot_data["admin_user_ids"] = admin_user_ids
    application.bot_data["rate_limiter"] = RateLimiter(
        per_minute=settings.rate_limit_per_minute,
        per_day=settings.rate_limit_per_day,
    )
    application.bot_data["ui_rate_limiter"] = RateLimiter(
        per_minute=max(20, settings.rate_limit_per_minute * 3),
        per_day=max(200, settings.rate_limit_per_day * 3),
    )
    application.bot_data["history"] = defaultdict(lambda: deque(maxlen=settings.history_size))
    application.bot_data["history_size"] = settings.history_size
    application.bot_data["message_limit"] = settings.telegram_message_limit
    application.bot_data["settings"] = settings
    application.bot_data["openai_client"] = openai_client
    application.bot_data["start_time"] = time.monotonic()
    application.bot_data["dialog_memory"] = dialog_memory
    application.bot_data["action_store"] = actions.ActionStore(
        ttl_seconds=settings.action_ttl_seconds,
        max_items=settings.action_max_size,
    )
    wizard_store = WizardStore(
        settings.wizard_store_path,
        timeout_seconds=settings.wizard_timeout_seconds,
    )
    application.bot_data["wizard_manager"] = wizard.WizardManager(wizard_store)
    if not application.job_queue:
        logging.getLogger(__name__).warning("JobQueue not configured; reminders will run without it.")

    async def _restore_reminders(app: Application) -> None:
        if not settings.reminders_enabled:
            logging.getLogger(__name__).info("Reminders disabled by config")
            return
        await reminder_scheduler.restore_all()

    application.post_init = _restore_reminders

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(CommandHandler("ping", handlers.ping))
    application.add_handler(CommandHandler("tasks", handlers.tasks))
    application.add_handler(CommandHandler("task", handlers.task))
    application.add_handler(CommandHandler("reminders", handlers.reminders))
    application.add_handler(CommandHandler("wtest", handlers.wtest))
    application.add_handler(CommandHandler("wtest2", handlers.wtest2))
    application.add_handler(CommandHandler("calendar_add", handlers.calendar_add_command))
    application.add_handler(CommandHandler("cancel_wizard", handlers.cancel_wizard))
    application.add_handler(CommandHandler("menu", handlers.menu_command))
    application.add_handler(CallbackQueryHandler(handlers.static_callback, pattern="^cb:"))
    application.add_handler(CallbackQueryHandler(handlers.wiz_callback, pattern="^wiz:"))
    application.add_handler(CallbackQueryHandler(handlers.action_callback))
    application.add_handler(MessageHandler(filters.PHOTO, handlers.photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.chat))
    application.add_handler(MessageHandler(filters.COMMAND, handlers.unknown_command))
    application.add_error_handler(handlers.error_handler)

    logging.getLogger(__name__).info("Bot started")
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    application.run_polling()


if __name__ == "__main__":
    main()
