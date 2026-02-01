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


def _split_text(text: str, max_len: int = 4000) -> list[str]:
    chunks: list[str] = []
    remaining = text or ""
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, max_len + 1)
        if split_at == -1:
            split_at = remaining.rfind(" ", 0, max_len + 1)
        if split_at <= 0:
            split_at = max_len
        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:max_len]
            split_at = max_len
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip("\n ")
    return chunks


async def _reply_long(update: Update, text: str) -> None:
    if not update.message:
        return
    for chunk in _split_text(text):
        await update.message.reply_text(chunk)


async def _guard_access(update: Update, orchestrator: Orchestrator) -> bool:
    user_id = update.effective_user.id if update.effective_user else 0
    if not orchestrator.is_allowed(user_id):
        await _reply_long(update, "Доступ запрещён.")
        return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    metadata = orchestrator.config.get("system_metadata", {})
    title = metadata.get("title", "Orchestrator")
    version = metadata.get("version", "unknown")
    access_note = ""
    if orchestrator.is_access_restricted():
        access_note = "\nДоступ ограничен whitelist пользователей."

    message = (
        "Привет! Я бот-оркестратор задач.\n"
        f"Конфигурация: {title} (v{version}).\n"
        "Команды: /help, /ping, /tasks, /task, /last, /ask, /search.\n"
        "Можно писать обычные сообщения — отвечу через LLM."
    )
    await _reply_long(update, message + access_note)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    access_note = ""
    if orchestrator.is_access_restricted():
        access_note = "\n\nДоступ ограничен whitelist пользователей."
    await _reply_long(
        update,
        "Доступные команды:\n"
        "/start — приветствие и статус\n"
        "/help — помощь\n"
        "/ping — pong + версия/время\n"
        "/tasks — список задач\n"
        "/task <name> <payload> — выполнить задачу\n"
        "/last — последняя задача\n"
        "/ask <текст> — задать вопрос LLM\n"
        "/search <текст> — поиск через LLM\n\n"
        "Примеры:\n"
        "/task upper hello\n"
        "/task json_pretty {\"a\": 1}\n"
        "/ask Привет!\n"
        "search Путин биография\n"
        "Или просто напишите сообщение без команды."
        + access_note
    )


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, orchestrator):
        return
    metadata = orchestrator.config.get("system_metadata", {})
    version = metadata.get("version", "unknown")
    now = datetime.now(timezone.utc).isoformat()
    await _reply_long(update, f"pong (v{version}) {now}")


async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, orchestrator):
        return
    available = orchestrator.list_tasks()
    if not available:
        await _reply_long(update, "Нет доступных задач.")
        return
    lines = [f"• {task.name}: {task.description}" for task in available]
    await _reply_long(update, "Доступные задачи:\n" + "\n".join(lines))


async def task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, orchestrator):
        return
    args = context.args
    if not args:
        await _reply_long(update, "Укажите имя задачи и payload.")
        return
    if len(args) == 1:
        await _reply_long(update, "Нужно передать payload. Пример: /task upper hello")
        return

    task_name = args[0]
    payload = " ".join(args[1:]).strip()
    if not payload:
        await _reply_long(update, "Payload не может быть пустым.")
        return

    user_id = update.effective_user.id if update.effective_user else 0
    execution = orchestrator.execute_task(user_id, task_name, payload)

    await _reply_long(
        update,
        "Результат:\n"
        f"Задача: {execution.task_name}\n"
        f"Статус: {execution.status}\n"
        f"Ответ: {execution.result}"
    )


async def last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, orchestrator):
        return
    storage = _get_storage(context)
    user_id = update.effective_user.id if update.effective_user else 0
    record = storage.get_last_execution(user_id)
    if not record:
        await _reply_long(update, "История пуста.")
        return

    await _reply_long(
        update,
        "Последняя задача:\n"
        f"Дата: {record['timestamp']}\n"
        f"Задача: {record['task_name']}\n"
        f"Статус: {record['status']}\n"
        f"Payload: {record['payload']}\n"
        f"Ответ: {record['result']}"
    )


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, orchestrator):
        return
    prompt = " ".join(context.args).strip()
    if not prompt:
        await _reply_long(update, "Введите текст запроса. Пример: /ask Привет")
        return

    user_id = update.effective_user.id if update.effective_user else 0
    execution = await orchestrator.ask_llm(user_id, prompt)
    if execution.status == "error":
        await _reply_long(update, f"Ошибка: {execution.result}")
        return
    await _reply_long(update, execution.result)


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, orchestrator):
        return
    prompt = " ".join(context.args).strip()
    if not prompt:
        await _reply_long(update, "Введите текст запроса. Пример: /search Новости")
        return

    user_id = update.effective_user.id if update.effective_user else 0
    execution = await orchestrator.search_llm(user_id, prompt)
    if execution.status == "error":
        await _reply_long(update, f"Ошибка: {execution.result}")
        return
    await _reply_long(update, execution.result)


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, orchestrator):
        return
    message = update.message.text if update.message else ""
    prompt = message.strip()
    if not prompt:
        return

    user_id = update.effective_user.id if update.effective_user else 0
    execution = await orchestrator.handle_text(user_id, prompt)
    if execution.status == "error":
        await _reply_long(update, f"Ошибка: {execution.result}")
        return
    await _reply_long(update, execution.result)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.exception("Unhandled exception", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await _reply_long(update, "Произошла ошибка. Попробуйте позже.")
