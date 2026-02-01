from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from app.core.orchestrator import Orchestrator
from app.infra.storage import TaskStorage

LOGGER = logging.getLogger(__name__)


def _get_orchestrator(context: ContextTypes.DEFAULT_TYPE) -> Orchestrator:
    return context.application.bot_data["orchestrator"]


def _get_storage(context: ContextTypes.DEFAULT_TYPE) -> TaskStorage:
    return context.application.bot_data["storage"]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    metadata = orchestrator.config.get("system_metadata", {})
    title = metadata.get("title", "Orchestrator")
    version = metadata.get("version", "unknown")

    message = (
        "Привет! Я бот-оркестратор задач.\n"
        f"Конфигурация: {title} (v{version}).\n"
        "Команды: /help, /ping, /tasks, /task, /last, /ask.\n"
        "Можно писать обычные сообщения — отвечу через LLM."
    )
    await update.message.reply_text(message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Доступные команды:\n"
        "/start — приветствие и статус\n"
        "/help — помощь\n"
        "/ping — pong + версия/время\n"
        "/tasks — список задач\n"
        "/task <name> <payload> — выполнить задачу\n"
        "/last — последняя задача\n"
        "/ask <текст> — задать вопрос LLM\n\n"
        "Примеры:\n"
        "/task upper hello\n"
        "/task json_pretty {\"a\": 1}\n"
        "/ask Привет!\n"
        "Или просто напишите сообщение без команды."
    )


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    metadata = orchestrator.config.get("system_metadata", {})
    version = metadata.get("version", "unknown")
    now = datetime.now(timezone.utc).isoformat()
    await update.message.reply_text(f"pong (v{version}) {now}")


async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    available = orchestrator.list_tasks()
    if not available:
        await update.message.reply_text("Нет доступных задач.")
        return
    lines = [f"• {task.name}: {task.description}" for task in available]
    await update.message.reply_text("Доступные задачи:\n" + "\n".join(lines))


async def task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    args = context.args
    if not args:
        await update.message.reply_text("Укажите имя задачи и payload.")
        return
    if len(args) == 1:
        await update.message.reply_text("Нужно передать payload. Пример: /task upper hello")
        return

    task_name = args[0]
    payload = " ".join(args[1:]).strip()
    if not payload:
        await update.message.reply_text("Payload не может быть пустым.")
        return

    user_id = update.effective_user.id if update.effective_user else 0
    execution = orchestrator.execute_task(user_id, task_name, payload)

    await update.message.reply_text(
        "Результат:\n"
        f"Задача: {execution.task_name}\n"
        f"Статус: {execution.status}\n"
        f"Ответ: {execution.result}"
    )


async def last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = _get_storage(context)
    user_id = update.effective_user.id if update.effective_user else 0
    record = storage.get_last_execution(user_id)
    if not record:
        await update.message.reply_text("История пуста.")
        return

    await update.message.reply_text(
        "Последняя задача:\n"
        f"Дата: {record['timestamp']}\n"
        f"Задача: {record['task_name']}\n"
        f"Статус: {record['status']}\n"
        f"Payload: {record['payload']}\n"
        f"Ответ: {record['result']}"
    )


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    prompt = " ".join(context.args).strip()
    if not prompt:
        await update.message.reply_text("Введите текст запроса. Пример: /ask Привет")
        return

    user_id = update.effective_user.id if update.effective_user else 0
    execution = await orchestrator.ask_llm(user_id, prompt)
    if execution.status == "error":
        await update.message.reply_text(f"Ошибка: {execution.result}")
        return
    await update.message.reply_text(execution.result)


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    message = update.message.text if update.message else ""
    prompt = message.strip()
    if not prompt:
        return

    user_id = update.effective_user.id if update.effective_user else 0
    execution = await orchestrator.ask_llm(user_id, prompt)
    if execution.status == "error":
        await update.message.reply_text(f"Ошибка: {execution.result}")
        return
    await update.message.reply_text(execution.result)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.exception("Unhandled exception", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await update.message.reply_text("Произошла ошибка. Попробуйте позже.")
