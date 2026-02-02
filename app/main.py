from __future__ import annotations

import logging
from collections import defaultdict, deque

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.bot import handlers
from app.core.orchestrator import Orchestrator, load_orchestrator_config
from app.infra.access import AccessController
from app.infra.config import load_settings
from app.infra.llm import OpenAIClient, PerplexityClient
from app.infra.rate_limit import RateLimiter
from app.infra.storage import TaskStorage


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
    allowed_user_ids = settings.allowed_user_ids
    access = AccessController(allowed_user_ids=allowed_user_ids)

    rate_limits = config.get("rate_limits", {}).get("llm", {})
    per_minute = settings.llm_per_minute
    if per_minute is None:
        per_minute = rate_limits.get("per_minute")
    per_day = settings.llm_per_day
    if per_day is None:
        per_day = rate_limits.get("per_day")
    rate_limiter = RateLimiter(per_minute=per_minute, per_day=per_day)

    orchestrator = Orchestrator(
        config=config,
        storage=storage,
        llm_client=llm_client,
        access=access,
        rate_limiter=rate_limiter,
        llm_history_turns=settings.llm_history_turns,
        llm_model=settings.openai_model if openai_client else settings.perplexity_model,
    )

    application = Application.builder().token(settings.bot_token).build()
    application.bot_data["orchestrator"] = orchestrator
    application.bot_data["storage"] = storage
    application.bot_data["allowed_user_ids"] = allowed_user_ids
    application.bot_data["rate_limiter"] = RateLimiter(
        per_minute=settings.rate_limit_per_minute,
        per_day=settings.rate_limit_per_day,
    )
    application.bot_data["history"] = defaultdict(lambda: deque(maxlen=settings.history_size))
    application.bot_data["history_size"] = settings.history_size
    application.bot_data["message_limit"] = settings.telegram_message_limit
    application.bot_data["settings"] = settings
    application.bot_data["openai_client"] = openai_client

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(CommandHandler("ping", handlers.ping))
    application.add_handler(CommandHandler("tasks", handlers.tasks))
    application.add_handler(CommandHandler("task", handlers.task))
    application.add_handler(CommandHandler("last", handlers.last))
    application.add_handler(CommandHandler("ask", handlers.ask))
    application.add_handler(CommandHandler("search", handlers.search))
    application.add_handler(CommandHandler("image", handlers.image))
    application.add_handler(CommandHandler("selfcheck", handlers.selfcheck))
    application.add_handler(MessageHandler(filters.PHOTO, handlers.photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.chat))
    application.add_error_handler(handlers.error_handler)

    logging.getLogger(__name__).info("Bot started")
    application.run_polling()


if __name__ == "__main__":
    main()
