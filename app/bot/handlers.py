from __future__ import annotations

import asyncio
import io
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from functools import wraps

import telegram
from telegram import Update
from telegram.ext import ContextTypes
from PIL import Image
import pytesseract

from app.core.orchestrator import Orchestrator, OrchestratorResult
from app.infra.messaging import safe_send_text
from app.infra.llm.openai_client import OpenAIClient
from app.infra.rate_limiter import RateLimiter
from app.infra.request_context import log_request, set_status, start_request
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


def _get_openai_client(context: ContextTypes.DEFAULT_TYPE) -> OpenAIClient | None:
    return context.application.bot_data.get("openai_client")


def _with_error_handling(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]],
) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        request_context = start_request(update, context)
        try:
            await handler(update, context)
        except Exception as exc:
            set_status(context, "error")
            await _handle_exception(update, context, exc)
        finally:
            log_request(LOGGER, request_context)

    return wrapper


async def _handle_exception(update: Update, context: ContextTypes.DEFAULT_TYPE, error: Exception) -> None:
    try:
        await context.application.process_error(update, error)
    except Exception:
        LOGGER.exception("Failed to forward exception to error handler")


def _format_wait_time(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "немного позже"
    if seconds < 60:
        return f"{int(seconds)} сек."
    if seconds < 3600:
        minutes = int(seconds // 60) or 1
        return f"{minutes} мин."
    hours = int(seconds // 3600) or 1
    return f"{hours} ч."


def _format_uptime(start_time: float) -> str:
    elapsed = max(0.0, time.monotonic() - start_time)
    days, rem = divmod(int(elapsed), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or parts:
        parts.append(f"{hours}h")
    if minutes or parts:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


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
        set_status(context, "error")
        await safe_send_text(update, context, "Доступ запрещён.")
        return False
    rate_limiter = _get_rate_limiter(context)
    result = await rate_limiter.check(user_id)
    if not result.allowed:
        set_status(context, "ratelimited")
        wait_time = _format_wait_time(result.retry_after)
        if result.scope == "day":
            message = f"Лимит запросов на сегодня. Попробуй через {wait_time}."
        else:
            message = f"Слишком часто. Попробуй через {wait_time}."
        await safe_send_text(update, context, message)
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
    await safe_send_text(update, context, response)


def _build_user_context(update: Update) -> dict[str, int]:
    user_id = update.effective_user.id if update.effective_user else 0
    return {"user_id": user_id}


def _log_orchestrator_result(
    user_id: int,
    prompt: str,
    result: OrchestratorResult,
) -> None:
    LOGGER.info(
        "Orchestrator result: user_id=%s intent=%s mode=%s status=%s prompt_len=%s response_len=%s sources=%s",
        user_id,
        result.intent,
        result.mode,
        result.status,
        len(prompt),
        len(result.text),
        len(result.sources),
    )


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
        "Команды: /help, /ping, /tasks, /task, /last, /ask, /search, /summary, /facts_on, /facts_off, /image.\n"
        "Можно писать обычные сообщения — верну ответ LLM.\n"
        "Суммаризация: summary: <текст> или /summary <текст>.\n"
        "Режим фактов: /facts_on и /facts_off.\n"
        "Отправьте фото, чтобы распознать текст.\n"
        "Локальные команды: echo, upper, json_pretty.\n"
        "Служебные команды: /selfcheck, /health."
    )
    await safe_send_text(update, context, message + access_note)


@_with_error_handling
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    access_note = ""
    if orchestrator.is_access_restricted():
        access_note = "\n\nДоступ ограничен whitelist пользователей."
    await safe_send_text(
        update,
        context,
        "Доступные команды:\n"
        "/start — приветствие и статус\n"
        "/help — помощь\n"
        "/ping — pong + версия/время\n"
        "/tasks — список задач\n"
        "/task <name> <payload> — выполнить задачу\n"
        "/last — последняя задача\n"
        "/ask <текст> — ответ LLM\n"
        "/search <текст> — ответ LLM с поиском\n"
        "/summary <текст> — краткое резюме (LLM)\n"
        "/facts_on — включить режим фактов\n"
        "/facts_off — выключить режим фактов\n"
        "/image <описание> — генерация изображения\n"
        "/selfcheck — проверка конфигурации\n"
        "/health — диагностика сервера\n\n"
        "Примеры:\n"
        "/task upper hello\n"
        "/task json_pretty {\"a\": 1}\n"
        "/ask Привет!\n"
        "search Путин биография\n"
        "summary: большой текст для сжатия\n"
        "echo привет\n"
        "upper привет\n"
        "json_pretty {\"a\":1}\n"
        "Или просто напишите сообщение без команды.\n"
        "Отправьте фото, чтобы распознать текст."
        + access_note,
    )


@_with_error_handling
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_send_text(
        update,
        context,
        f"user_id={update.effective_user.id} chat_id={update.effective_chat.id}",
    )
    return


    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    metadata = orchestrator.config.get("system_metadata", {})
    version = metadata.get("version", "unknown")
    now = datetime.now(timezone.utc).isoformat()
    await safe_send_text(update, context, f"pong (v{version}) {now}")


@_with_error_handling
async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    available = orchestrator.list_tasks()
    if not available:
        await safe_send_text(update, context, "Нет доступных задач.")
        return
    lines = [f"• {task.name}: {task.description}" for task in available]
    await safe_send_text(
        update,
        context,
        "Доступные задачи:\n" + "\n".join(lines),
    )


@_with_error_handling
async def task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    args = context.args
    if not args:
        await safe_send_text(update, context, "Укажите имя задачи и payload.")
        return
    if len(args) == 1:
        await safe_send_text(
            update,
            context,
            "Нужно передать payload. Пример: /task upper hello",
        )
        return

    task_name = args[0]
    payload = " ".join(args[1:]).strip()
    if not payload:
        await safe_send_text(update, context, "Payload не может быть пустым.")
        return

    user_id = update.effective_user.id if update.effective_user else 0
    try:
        execution = orchestrator.execute_task(user_id, task_name, payload)
    except Exception as exc:
        set_status(context, "error")
        await _handle_exception(update, context, exc)
        return

    await safe_send_text(
        update,
        context,
        "Результат:\n"
        f"Задача: {execution.task_name}\n"
        f"Статус: {execution.status}\n"
        f"Ответ: {execution.result}",
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
        await safe_send_text(update, context, "История пуста.")
        return

    await safe_send_text(
        update,
        context,
        "Последняя задача:\n"
        f"Дата: {record['timestamp']}\n"
        f"Задача: {record['task_name']}\n"
        f"Статус: {record['status']}\n"
        f"Payload: {record['payload']}\n"
        f"Ответ: {record['result']}",
    )


@_with_error_handling
async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    prompt = " ".join(context.args).strip()
    if not prompt:
        await safe_send_text(
            update,
            context,
            "Введите текст запроса. Пример: /ask Привет",
        )
        return
    user_id = update.effective_user.id if update.effective_user else 0
    try:
        result = await orchestrator.handle(f"/ask {prompt}", _build_user_context(update))
    except Exception as exc:
        set_status(context, "error")
        await _handle_exception(update, context, exc)
        return
    _log_orchestrator_result(user_id, prompt, result)
    await safe_send_text(update, context, result.text)


@_with_error_handling
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    prompt = " ".join(context.args).strip()
    if not prompt:
        await safe_send_text(
            update,
            context,
            "Введите текст запроса. Пример: /search Новости",
        )
        return
    user_id = update.effective_user.id if update.effective_user else 0
    try:
        result = await orchestrator.handle(f"/search {prompt}", _build_user_context(update))
    except Exception as exc:
        set_status(context, "error")
        await _handle_exception(update, context, exc)
        return
    _log_orchestrator_result(user_id, prompt, result)
    await safe_send_text(update, context, result.text)


@_with_error_handling
async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    prompt = " ".join(context.args).strip()
    user_id = update.effective_user.id if update.effective_user else 0
    try:
        payload = f"/summary {prompt}" if prompt else "/summary"
        result = await orchestrator.handle(payload, _build_user_context(update))
    except Exception as exc:
        set_status(context, "error")
        await _handle_exception(update, context, exc)
        return
    _log_orchestrator_result(user_id, prompt, result)
    await safe_send_text(update, context, result.text)


@_with_error_handling
async def facts_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    orchestrator.set_facts_only(user_id, True)
    await safe_send_text(update, context, "Режим фактов включён. Буду отвечать только с источниками.")


@_with_error_handling
async def facts_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    orchestrator.set_facts_only(user_id, False)
    await safe_send_text(update, context, "Режим фактов выключён. Можно отвечать без источников.")


def _extract_text_from_image(image_bytes: bytes) -> str:
    with Image.open(io.BytesIO(image_bytes)) as image:
        return pytesseract.image_to_string(image).strip()


@_with_error_handling
async def image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    prompt = " ".join(context.args).strip()
    if not prompt:
        await safe_send_text(
            update,
            context,
            "Укажите описание изображения. Пример: /image Слон в космосе",
        )
        return
    openai_client = _get_openai_client(context)
    if openai_client is None or not openai_client.api_key:
        await safe_send_text(update, context, "Генерация изображений не настроена.")
        return
    response = await openai_client.create_image(prompt=prompt)
    data = response.get("data") or []
    image_url = data[0].get("url") if data else None
    if not image_url:
        await safe_send_text(update, context, "Не удалось получить изображение.")
        return
    if update.message:
        await update.message.reply_photo(image_url)


@_with_error_handling
async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    if not update.message or not update.message.photo:
        return
    file = await update.message.photo[-1].get_file()
    buf = io.BytesIO()
    await file.download_to_memory(out=buf)
    image_bytes = buf.getvalue()
    loop = asyncio.get_running_loop()
    try:
        text = await loop.run_in_executor(None, _extract_text_from_image, bytes(image_bytes))
    except Exception:
        LOGGER.exception("OCR failed")
        await safe_send_text(update, context, "Не удалось распознать текст.")
        return
    if not text:
        await safe_send_text(update, context, "Текст не найден.")
        return
    await safe_send_text(update, context, text)


@_with_error_handling
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    message = update.message.text if update.message else ""
    prompt = message.strip()
    if not prompt:
        return
    user_id = update.effective_user.id if update.effective_user else 0
    try:
        result = await orchestrator.handle(prompt, _build_user_context(update))
    except Exception as exc:
        set_status(context, "error")
        await _handle_exception(update, context, exc)
        return
    _log_orchestrator_result(user_id, prompt, result)
    await safe_send_text(update, context, result.text)


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
    await safe_send_text(update, context, message)


@_with_error_handling
async def health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    settings = context.application.bot_data["settings"]
    rate_limiter = _get_rate_limiter(context)
    start_time = context.application.bot_data.get("start_time", time.monotonic())
    uptime = _format_uptime(start_time)
    python_version = sys.version.split()[0]
    telegram_version = telegram.__version__
    message = (
        "Health:\n"
        f"Uptime: {uptime}\n"
        f"Rate limits: {rate_limiter.per_minute}/min, {rate_limiter.per_day}/day\n"
        f"Python: {python_version}\n"
        f"Telegram: {telegram_version}\n"
        f"Orchestrator config: {settings.orchestrator_config_path}\n"
        f"Rate limit cache: {rate_limiter.cache_size} users"
    )
    await safe_send_text(update, context, message)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_status(context, "error")
    LOGGER.exception("Unhandled exception", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await safe_send_text(update, context, "Ошибка на сервере. Попробуй ещё раз.")
