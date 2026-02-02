from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from functools import wraps

from telegram import Message, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from app.core.orchestrator import Orchestrator
from app.infra.rate_limit import RateLimiter
from app.infra.storage import TaskStorage

LOGGER = logging.getLogger(__name__)


def _get_orchestrator(context: ContextTypes.DEFAULT_TYPE) -> Orchestrator:
    return context.application.bot_data["orchestrator"]


def _get_storage(context: ContextTypes.DEFAULT_TYPE) -> TaskStorage:
    return context.application.bot_data["storage"]


def _get_allowed_user_ids(context: ContextTypes.DEFAULT_TYPE) -> set[int]:
    return context.application.bot_data["allowed_user_ids"]


def _get_rate_limiter(context: ContextTypes.DEFAULT_TYPE) -> RateLimiter:
    return context.application.bot_data["rate_limiter"]


def _get_history(context: ContextTypes.DEFAULT_TYPE) -> dict[int, list[tuple[datetime, str, str]]]:
    return context.application.bot_data["history"]


def _get_message_limit(context: ContextTypes.DEFAULT_TYPE) -> int:
    return context.application.bot_data["message_limit"]


def split_text(text: str, max_len: int) -> list[str]:
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


async def safe_reply(target: Update | Message, text: str, max_len: int) -> None:
    message = target if isinstance(target, Message) else target.message
    if not message:
        return
    for chunk in split_text(text, max_len=max_len):
        try:
            await message.reply_text(chunk)
        except BadRequest as exc:
            if "Message is too long" in str(exc):
                LOGGER.warning("Telegram rejected message chunk as too long; splitting further.")
                for subchunk in split_text(chunk, max_len=2000):
                    await message.reply_text(subchunk)
                continue
            LOGGER.exception("Failed to send message chunk: %s", exc)
            break


def _with_error_handling(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]],
) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await handler(update, context)
        except Exception:
            LOGGER.exception("Handler failed")
            await safe_reply(update, "Ошибка обработки. Попробуй ещё раз.", _get_message_limit(context))

    return wrapper


async def _guard_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    user_id = user.id if user else 0
    allowed_user_ids = _get_allowed_user_ids(context)
    if user_id not in allowed_user_ids:
        LOGGER.warning(
            "Access denied: user_id=%s username=%s",
            user_id,
            user.username if user else "unknown",
        )
        await safe_reply(update, "Доступ запрещён.", _get_message_limit(context))
        return False
    rate_limiter = _get_rate_limiter(context)
    allowed, _ = rate_limiter.check(user_id)
    if not allowed:
        await safe_reply(update, "Лимит запросов. Попробуй позже.", _get_message_limit(context))
        return False
    return True


def _append_history(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    role: str,
    text: str,
) -> list[tuple[datetime, str, str]]:
    history_map = _get_history(context)
    history = history_map[user_id]
    history.append((datetime.now(timezone.utc), role, text))
    return history


def _format_history(history: list[tuple[datetime, str, str]]) -> str:
    if not history:
        return "Ок. История пуста."
    lines = [f"{role}: {text}" for _, role, text in history]
    return "Ок. Последние сообщения:\n" + "\n".join(lines)


async def _reply_with_history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str,
) -> None:
    user_id = update.effective_user.id if update.effective_user else 0
    history = _append_history(context, user_id, "user", prompt)
    response = _format_history(history)
    _append_history(context, user_id, "assistant", response)
    await safe_reply(update, response, _get_message_limit(context))


@_with_error_handling
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
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
        "Можно писать обычные сообщения — верну контекст диалога.\n"
        "Локальные команды: echo, upper, json_pretty.\n"
        "Служебная команда: /selfcheck."
    )
    await safe_reply(update, message + access_note, _get_message_limit(context))


@_with_error_handling
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    access_note = ""
    if orchestrator.is_access_restricted():
        access_note = "\n\nДоступ ограничен whitelist пользователей."
    await safe_reply(
        update,
        "Доступные команды:\n"
        "/start — приветствие и статус\n"
        "/help — помощь\n"
        "/ping — pong + версия/время\n"
        "/tasks — список задач\n"
        "/task <name> <payload> — выполнить задачу\n"
        "/last — последняя задача\n"
        "/ask <текст> — тестовый ответ с контекстом\n"
        "/search <текст> — тестовый ответ с контекстом\n"
        "/selfcheck — проверка конфигурации\n\n"
        "Примеры:\n"
        "/task upper hello\n"
        "/task json_pretty {\"a\": 1}\n"
        "/ask Привет!\n"
        "search Путин биография\n"
        "echo привет\n"
        "upper привет\n"
        "json_pretty {\"a\":1}\n"
        "Или просто напишите сообщение без команды."
        + access_note,
        _get_message_limit(context),
    )


@_with_error_handling
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    metadata = orchestrator.config.get("system_metadata", {})
    version = metadata.get("version", "unknown")
    now = datetime.now(timezone.utc).isoformat()
    await safe_reply(update, f"pong (v{version}) {now}", _get_message_limit(context))


@_with_error_handling
async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    available = orchestrator.list_tasks()
    if not available:
        await safe_reply(update, "Нет доступных задач.", _get_message_limit(context))
        return
    lines = [f"• {task.name}: {task.description}" for task in available]
    await safe_reply(
        update,
        "Доступные задачи:\n" + "\n".join(lines),
        _get_message_limit(context),
    )


@_with_error_handling
async def task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    args = context.args
    if not args:
        await safe_reply(update, "Укажите имя задачи и payload.", _get_message_limit(context))
        return
    if len(args) == 1:
        await safe_reply(
            update,
            "Нужно передать payload. Пример: /task upper hello",
            _get_message_limit(context),
        )
        return

    task_name = args[0]
    payload = " ".join(args[1:]).strip()
    if not payload:
        await safe_reply(update, "Payload не может быть пустым.", _get_message_limit(context))
        return

    user_id = update.effective_user.id if update.effective_user else 0
    execution = orchestrator.execute_task(user_id, task_name, payload)

    await safe_reply(
        update,
        "Результат:\n"
        f"Задача: {execution.task_name}\n"
        f"Статус: {execution.status}\n"
        f"Ответ: {execution.result}",
        _get_message_limit(context),
    )


@_with_error_handling
async def last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    storage = _get_storage(context)
    user_id = update.effective_user.id if update.effective_user else 0
    record = storage.get_last_execution(user_id)
    if not record:
        await safe_reply(update, "История пуста.", _get_message_limit(context))
        return

    await safe_reply(
        update,
        "Последняя задача:\n"
        f"Дата: {record['timestamp']}\n"
        f"Задача: {record['task_name']}\n"
        f"Статус: {record['status']}\n"
        f"Payload: {record['payload']}\n"
        f"Ответ: {record['result']}",
        _get_message_limit(context),
    )


@_with_error_handling
async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    prompt = " ".join(context.args).strip()
    if not prompt:
        await safe_reply(
            update,
            "Введите текст запроса. Пример: /ask Привет",
            _get_message_limit(context),
        )
        return
    await _reply_with_history(update, context, prompt)


@_with_error_handling
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    prompt = " ".join(context.args).strip()
    if not prompt:
        await safe_reply(
            update,
            "Введите текст запроса. Пример: /search Новости",
            _get_message_limit(context),
        )
        return
    await _reply_with_history(update, context, prompt)


@_with_error_handling
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    message = update.message.text if update.message else ""
    prompt = message.strip()
    if not prompt:
        return
    await _reply_with_history(update, context, prompt)


@_with_error_handling
async def selfcheck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    settings = context.application.bot_data["settings"]
    allowed_user_ids = sorted(settings.allowed_user_ids)
    if allowed_user_ids:
        allowed_summary = f"ok ({len(allowed_user_ids)}): {', '.join(map(str, allowed_user_ids))}"
    else:
        allowed_summary = "empty (доступ закрыт)"
    message = (
        "Self-check:\n"
        f"ALLOWED_USER_IDS: {allowed_summary}\n"
        f"RATE_LIMIT_PER_MINUTE: {settings.rate_limit_per_minute}\n"
        f"RATE_LIMIT_PER_DAY: {settings.rate_limit_per_day}\n"
        f"HISTORY_SIZE: {settings.history_size}\n"
        f"TELEGRAM_MESSAGE_LIMIT: {settings.telegram_message_limit}"
    )
    await safe_reply(update, message, _get_message_limit(context))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.exception("Unhandled exception", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await safe_reply(update, "Ошибка обработки. Попробуй ещё раз.", _get_message_limit(context))
