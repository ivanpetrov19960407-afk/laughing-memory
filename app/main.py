from __future__ import annotations

import logging

from telegram.ext import Application, CommandHandler

from app.bot import handlers
from app.core.orchestrator import Orchestrator, load_orchestrator_config
from app.infra.config import load_settings
from app.infra.storage import TaskStorage


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    settings = load_settings()
    config = load_orchestrator_config(settings.orchestrator_config_path)
    storage = TaskStorage(settings.db_path)
    orchestrator = Orchestrator(config=config, storage=storage)

    application = Application.builder().token(settings.bot_token).build()
    application.bot_data["orchestrator"] = orchestrator
    application.bot_data["storage"] = storage

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(CommandHandler("ping", handlers.ping))
    application.add_handler(CommandHandler("tasks", handlers.tasks))
    application.add_handler(CommandHandler("task", handlers.task))
    application.add_handler(CommandHandler("last", handlers.last))
    application.add_error_handler(handlers.error_handler)

    logging.getLogger(__name__).info("Bot started")
    application.run_polling()


if __name__ == "__main__":
    main()
