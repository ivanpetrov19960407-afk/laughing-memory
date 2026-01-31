import json
import logging
import os
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


LOGGER = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/orchestrator.json")


def load_orchestrator_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)


def get_config_path() -> Path:
    env_path = os.getenv("ORCHESTRATOR_CONFIG_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_CONFIG_PATH


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = context.application.bot_data.get("orchestrator_config", {})
    title = config.get("system_metadata", {}).get("title", "Оркестратор")
    version = config.get("system_metadata", {}).get("version", "unknown")
    await update.message.reply_text(
        "Привет! Я бот-оркестратор.\n"
        f"Конфигурация: {title} (v{version}).\n"
        "Команды: /help, /config, /task"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Доступные команды:\n"
        "/start — приветствие и статус конфигурации\n"
        "/config — показать краткое резюме конфигурации\n"
        "/task — принять задачу (после команды отправьте описание)"
    )


async def config_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = context.application.bot_data.get("orchestrator_config", {})
    metadata = config.get("system_metadata", {})
    execution = config.get("execution_config", {})
    await update.message.reply_text(
        "Текущая конфигурация:\n"
        f"• title: {metadata.get('title', 'n/a')}\n"
        f"• version: {metadata.get('version', 'n/a')}\n"
        f"• mode: {execution.get('mode', 'n/a')}\n"
        f"• max_agents: {execution.get('max_concurrent_agents', 'n/a')}\n"
        f"• max_iterations: {execution.get('default_max_iterations', 'n/a')}"
    )


async def task_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["awaiting_task"] = True
    await update.message.reply_text(
        "Опишите задачу одним сообщением. Я зарегистрирую её и верну статус."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting_task"):
        context.user_data["awaiting_task"] = False
        task_text = update.message.text.strip()
        await update.message.reply_text(
            "Задача получена.\n"
            f"Содержание: {task_text}\n"
            "Статус: очередь на обработку (демо)."
        )
        return

    await update.message.reply_text(
        "Я понимаю команды /start, /help, /config, /task."
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    config_path = get_config_path()
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found at {config_path}. Set ORCHESTRATOR_CONFIG_PATH."
        )

    orchestrator_config = load_orchestrator_config(config_path)

    application = Application.builder().token(token).build()
    application.bot_data["orchestrator_config"] = orchestrator_config

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("config", config_command))
    application.add_handler(CommandHandler("task", task_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    LOGGER.info("Bot started")
    application.run_polling()


if __name__ == "__main__":
    main()
