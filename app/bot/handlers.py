from __future__ import annotations

import asyncio
import io
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any

import telegram
from telegram import InputFile, Update
from telegram.ext import ContextTypes
from PIL import Image
import pytesseract

from app.bot import menu, routing
from app.bot.actions import ActionStore, StoredAction, build_inline_keyboard, parse_callback_token
from app.core import calendar_store
from app.core.calc import CalcError, parse_and_eval
from app.core.dialog_memory import DialogMemory, DialogMessage
from app.core.orchestrator import Orchestrator
from app.core.result import Action, OrchestratorResult, ensure_valid, error, ok, ratelimited, refused
from app.core.tools_calendar import list_calendar_items, list_reminders
from app.core.tools_llm import llm_check, llm_explain, llm_rewrite
from app.infra.allowlist import AllowlistStore
from app.infra.messaging import safe_send_text
from app.infra.llm.openai_client import OpenAIClient
from app.infra.rate_limiter import RateLimiter
from app.infra.request_context import get_request_context, log_request, set_status, start_request
from app.infra.storage import TaskStorage

LOGGER = logging.getLogger(__name__)


def _get_orchestrator(context: ContextTypes.DEFAULT_TYPE) -> Orchestrator:
    return context.application.bot_data["orchestrator"]


def _get_storage(context: ContextTypes.DEFAULT_TYPE) -> TaskStorage:
    return context.application.bot_data["storage"]


def _get_allowlist_store(context: ContextTypes.DEFAULT_TYPE) -> AllowlistStore:
    return context.application.bot_data["allowlist_store"]


def _get_admin_user_ids(context: ContextTypes.DEFAULT_TYPE) -> set[int]:
    return context.application.bot_data["admin_user_ids"]


def _get_rate_limiter(context: ContextTypes.DEFAULT_TYPE, *, bucket: str = "default") -> RateLimiter:
    if bucket == "ui":
        return context.application.bot_data["ui_rate_limiter"]
    return context.application.bot_data["rate_limiter"]


def _get_history(context: ContextTypes.DEFAULT_TYPE) -> dict[int, list[tuple[datetime, str, str]]]:
    return context.application.bot_data["history"]


def _get_dialog_memory(context: ContextTypes.DEFAULT_TYPE) -> DialogMemory | None:
    return context.application.bot_data.get("dialog_memory")


def _get_openai_client(context: ContextTypes.DEFAULT_TYPE) -> OpenAIClient | None:
    return context.application.bot_data.get("openai_client")


def _get_reminder_scheduler(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data.get("reminder_scheduler")


def _get_action_store(context: ContextTypes.DEFAULT_TYPE) -> ActionStore:
    store = context.application.bot_data.get("action_store")
    if isinstance(store, ActionStore):
        return store
    store = ActionStore()
    context.application.bot_data["action_store"] = store
    return store


async def _get_active_modes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    user_id = update.effective_user.id if update.effective_user else 0
    orchestrator = context.application.bot_data.get("orchestrator")
    facts_status = "unknown"
    if isinstance(orchestrator, Orchestrator) and user_id:
        facts_status = "on" if orchestrator.is_facts_only(user_id) else "off"

    dialog_memory = _get_dialog_memory(context)
    if dialog_memory is None or not user_id:
        context_status = "off" if dialog_memory is None else "unknown"
    else:
        context_status = "on" if await dialog_memory.is_enabled(user_id) else "off"

    settings = context.application.bot_data.get("settings")
    reminders_status = "unknown"
    if settings is not None and hasattr(settings, "reminders_enabled"):
        reminders_status = "on" if settings.reminders_enabled else "off"

    return f"facts={facts_status}, context={context_status}, reminders={reminders_status}"


async def _log_route(update: Update, context: ContextTypes.DEFAULT_TYPE, handler_name: str) -> None:
    user_id = update.effective_user.id if update.effective_user else 0
    if update.callback_query:
        message_type = "callback"
        command = "-"
        route = "callback"
    else:
        message = update.effective_message
        text = message.text if message and message.text else ""
        command = routing.normalize_command(text)
        if text:
            message_type = "command" if command else "text"
        else:
            message_type = "non_text"
        route = routing.resolve_text_route(text) if text else "non_text"
    modes = await _get_active_modes(update, context)
    LOGGER.info(
        "Route: user_id=%s type=%s command=%s handler=%s route=%s modes=%s",
        user_id,
        message_type,
        command or "-",
        handler_name,
        route,
        modes,
    )


def _with_error_handling(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]],
) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        request_context = start_request(update, context)
        try:
            await _log_route(update, context, handler.__name__)
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


async def _guard_access(update: Update, context: ContextTypes.DEFAULT_TYPE, *, bucket: str = "default") -> bool:
    user = update.effective_user
    user_id = user.id if user else 0
    if not _is_allowed_user(context, user_id):
        LOGGER.warning(
            "Access denied: user_id=%s username=%s chat_id=%s reason=not_allowed",
            user_id,
            user.username if user else "unknown",
            update.effective_chat.id if update.effective_chat else "unknown",
        )
        set_status(context, "error")
        await _send_access_denied(update, context, user_id)
        return False
    rate_limiter = _get_rate_limiter(context, bucket=bucket)
    result = await rate_limiter.check(user_id)
    if not result.allowed:
        set_status(context, "ratelimited")
        wait_time = _format_wait_time(result.retry_after)
        if result.scope == "day":
            message = f"Лимит запросов на сегодня. Попробуй через {wait_time}."
        else:
            message = f"Слишком часто. Попробуй через {wait_time}."
        result_message = ratelimited(
            message,
            intent="rate_limit",
            mode="local",
            debug={"scope": result.scope, "retry_after": result.retry_after},
        )
        await send_result(update, context, result_message)
        return False
    return True


def _is_allowed_user(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if user_id in _get_admin_user_ids(context):
        return True
    return _get_allowlist_store(context).is_allowed(user_id)


def _is_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    return user_id in _get_admin_user_ids(context)


async def _send_access_denied(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
) -> None:
    result = _build_simple_result(
        f"Доступ запрещён.\nТвой user_id: {user_id}",
        intent="access_denied",
        status="refused",
        mode="local",
    )
    await send_result(update, context, result)


async def _require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id if update.effective_user else 0
    if _is_admin(context, user_id):
        return True
    set_status(context, "error")
    result = _build_simple_result(
        "Недостаточно прав.",
        intent="access_denied.admin",
        status="refused",
        mode="local",
    )
    await send_result(update, context, result)
    return False


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
    result = _build_simple_result(response, intent="history", status="ok", mode="local")
    await send_result(update, context, result)


def _build_user_context(update: Update) -> dict[str, int]:
    user_id = update.effective_user.id if update.effective_user else 0
    return {"user_id": user_id}


def _build_menu_actions(context: ContextTypes.DEFAULT_TYPE, *, user_id: int) -> list[Action]:
    orchestrator = _get_orchestrator(context)
    facts_enabled = bool(user_id) and orchestrator.is_facts_only(user_id)
    return menu.build_menu_actions(facts_enabled=facts_enabled)


def _build_simple_result(
    text: str,
    *,
    intent: str,
    status: str = "ok",
    mode: str = "local",
    debug: dict[str, Any] | None = None,
) -> OrchestratorResult:
    if status == "ok":
        return ok(text, intent=intent, mode=mode, debug=debug)
    if status == "refused":
        return refused(text, intent=intent, mode=mode, debug=debug)
    if status == "ratelimited":
        return ratelimited(text, intent=intent, mode=mode, debug=debug)
    return error(text, intent=intent, mode=mode, debug=debug)


def _build_user_context_with_dialog(
    update: Update,
    *,
    dialog_context: str | None,
    dialog_message_count: int,
    request_id: str | None,
) -> dict[str, object]:
    user_id = update.effective_user.id if update.effective_user else 0
    payload: dict[str, object] = {"user_id": user_id}
    if dialog_context:
        payload["dialog_context"] = dialog_context
        payload["dialog_message_count"] = dialog_message_count
    if request_id:
        payload["request_id"] = request_id
    return payload


def _should_store_assistant_response(result: OrchestratorResult) -> bool:
    if result.status != "ok":
        return False
    lowered = result.text.lower()
    if "доступ запрещ" in lowered:
        return False
    if "traceback" in lowered or "stacktrace" in lowered or "stack trace" in lowered:
        return False
    if lowered.startswith("ошибка"):
        return False
    return True


def _drop_latest_user_message(
    messages: list[DialogMessage],
    prompt: str,
) -> list[DialogMessage]:
    if not messages:
        return messages
    last = messages[-1]
    if last.role == "user" and last.text.strip() == prompt.strip():
        return messages[:-1]
    return messages


async def _prepare_dialog_context(
    memory: DialogMemory | None,
    *,
    user_id: int,
    chat_id: int,
    prompt: str,
) -> tuple[str | None, int]:
    if memory is None:
        return None, 0
    if not await memory.is_enabled(user_id):
        return None, 0
    messages = await memory.get_context(user_id, chat_id)
    messages = _drop_latest_user_message(messages, prompt)
    if not messages:
        return None, 0
    return memory.format_context(messages), len(messages)


def _build_tool_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict[str, object]:
    user_id = update.effective_user.id if update.effective_user else 0
    return {"user_id": user_id, "orchestrator": _get_orchestrator(context)}


def _log_orchestrator_result(
    user_id: int,
    result: OrchestratorResult,
    *,
    request_id: str | None = None,
) -> None:
    LOGGER.info(
        "Orchestrator result: request_id=%s user_id=%s intent=%s mode=%s status=%s sources=%s actions=%s attachments=%s response_len=%s",
        request_id or "-",
        user_id,
        result.intent,
        result.mode,
        result.status,
        len(result.sources),
        len(result.actions),
        len(result.attachments),
        len(result.text),
    )
    if result.debug:
        LOGGER.info(
            "Orchestrator debug: request_id=%s user_id=%s intent=%s debug=%s",
            request_id or "-",
            user_id,
            result.intent,
            result.debug,
        )


async def _send_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None) -> None:
    await safe_send_text(update, context, text, reply_markup=reply_markup)


async def _send_attachments(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    attachments: list[Any],
) -> None:
    if not attachments:
        return
    message = update.effective_message if update else None
    if message is None:
        LOGGER.warning("Cannot send attachments: no message context.")
        return
    for attachment in attachments:
        attachment_type = getattr(attachment, "type", None) or attachment.get("type")
        name = getattr(attachment, "name", None) or attachment.get("name")
        payload_path = getattr(attachment, "path", None) or attachment.get("path")
        payload_bytes = getattr(attachment, "bytes", None) or attachment.get("bytes")
        payload_url = getattr(attachment, "url", None) or attachment.get("url")
        try:
            if payload_url:
                if attachment_type == "image":
                    await message.reply_photo(payload_url)
                else:
                    await message.reply_document(payload_url)
                continue
            if payload_bytes:
                file_obj = InputFile(io.BytesIO(payload_bytes), filename=name or "attachment")
            elif payload_path:
                file_obj = InputFile(payload_path, filename=name or "attachment")
            else:
                LOGGER.warning("Attachment missing payload: %s", attachment)
                continue
            if attachment_type == "image":
                await message.reply_photo(file_obj)
            else:
                await message.reply_document(file_obj)
        except Exception:
            LOGGER.exception("Failed to send attachment: %s", attachment)


async def send_result(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    result: OrchestratorResult,
    *,
    reply_markup=None,
) -> None:
    public_result = ensure_valid(result)
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    request_context = get_request_context(context)
    request_id = request_context.request_id if request_context else None
    if request_id:
        sent_key = f"send_result:{request_id}"
        if context.chat_data.get(sent_key):
            LOGGER.warning("send_result skipped duplicate: request_id=%s intent=%s", request_id, public_result.intent)
            return
        context.chat_data[sent_key] = True
    inline_keyboard = build_inline_keyboard(
        public_result.actions,
        store=_get_action_store(context),
        user_id=user_id,
        chat_id=chat_id,
    )
    effective_reply_markup = reply_markup if reply_markup is not None else inline_keyboard
    await _send_text(update, context, public_result.text, reply_markup=effective_reply_markup)
    await _send_attachments(update, context, public_result.attachments)


@_with_error_handling
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context, bucket="ui"):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    metadata = orchestrator.config.get("system_metadata", {})
    title = metadata.get("title", "Orchestrator")
    version = metadata.get("version", "unknown")
    access_note = ""
    if orchestrator.is_access_restricted():
        access_note = "\nДоступ ограничен whitelist пользователей."

    message = (
        "Привет! Я бот-оркестратор задач.\n"
        f"Конфигурация: {title} (v{version}).\n"
        "Команды: /help, /ping, /tasks, /task, /last, /ask, /search, /summary, /facts_on, /facts_off, /image, "
        "/check, /rewrite, /explain, /calc, /calendar, /reminders, /reminder_off, /reminder_on, /context_on, "
        "/context_off, /context_clear, /context_status.\n"
        "Можно писать обычные сообщения — верну ответ LLM.\n"
        "Суммаризация: summary: <текст> или /summary <текст>.\n"
        "Режим фактов: /facts_on и /facts_off.\n"
        "Отправьте фото, чтобы распознать текст.\n"
        "Локальные команды: echo, upper, json_pretty, calc, calendar.\n"
        "Служебные команды: /selfcheck, /health."
    )
    result = ok(
        message + access_note,
        intent="command.start",
        mode="local",
        actions=_build_menu_actions(context, user_id=user_id),
    )
    await send_result(update, context, result)


@_with_error_handling
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context, bucket="ui"):
        return
    access_note = ""
    if orchestrator.is_access_restricted():
        access_note = "\n\nДоступ ограничен whitelist пользователей."
    result = _build_simple_result(
        _build_help_text(access_note),
        intent="command.help",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    message = update.message.text if update.message else ""
    result = refused(
        "Неизвестная команда. Напиши /help.",
        intent="command.unknown",
        mode="local",
    )
    await send_result(update, context, result)


def _build_help_text(access_note: str) -> str:
    return (
        "Доступные команды:\n"
        "/start — приветствие и статус\n"
        "/help — помощь\n"
        "/menu — показать меню\n"
        "/ping — pong + версия/время\n"
        "/tasks — список задач\n"
        "/task <name> <payload> — выполнить задачу\n"
        "/last — последняя задача\n"
        "/ask <текст> — ответ LLM\n"
        "/search <текст> — ответ LLM с поиском\n"
        "/summary <текст> — краткое резюме (LLM)\n"
        "/facts_on — включить режим фактов\n"
        "/facts_off — выключить режим фактов\n"
        "/context_on — включить контекст диалога\n"
        "/context_off — выключить контекст диалога\n"
        "/context_clear — очистить историю контекста\n"
        "/context_status — статус контекста\n"
        "/image <описание> — генерация изображения\n"
        "/check <текст> — проверка текста (LLM)\n"
        "/rewrite <mode> <текст> — переписать текст (LLM)\n"
        "/explain <текст> — объяснить текст (LLM)\n"
        "/calc <expr> — калькулятор\n"
        "/calendar <cmd> — планер (add/list/today/week/del)\n"
        "/reminders [N] — ближайшие напоминания\n"
        "/reminder_off <id> — отключить напоминание\n"
        "/reminder_on <event_id> — включить напоминание\n"
        "/selfcheck — проверка конфигурации\n"
        "/health — диагностика сервера\n"
        "/allow <user_id> — добавить в whitelist (админ)\n"
        "/deny <user_id> — удалить из whitelist (админ)\n"
        "/allowlist — список whitelist (админ)\n\n"
        "Примеры:\n"
        "/task upper hello\n"
        "/task json_pretty {\"a\": 1}\n"
        "/ask Привет!\n"
        "/check текст\n"
        "/rewrite simple текст\n"
        "/explain текст\n"
        "/calc 2+2\n"
        "/calendar add 2026-02-05 18:30 -m 10 Врач\n"
        "/reminders 5\n"
        "search Путин биография\n"
        "summary: большой текст для сжатия\n"
        "echo привет\n"
        "upper привет\n"
        "json_pretty {\"a\":1}\n"
        "Или просто напишите сообщение без команды.\n"
        "Отправьте фото, чтобы распознать текст."
        + access_note
    )


async def _build_health_message(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int | None = None,
) -> str:
    settings = context.application.bot_data["settings"]
    rate_limiter = _get_rate_limiter(context)
    start_time = context.application.bot_data.get("start_time", time.monotonic())
    uptime = _format_uptime(start_time)
    python_version = sys.version.split()[0]
    telegram_version = telegram.__version__
    modes = "-"
    if user_id is not None:
        orchestrator = _get_orchestrator(context)
        dialog_memory = _get_dialog_memory(context)
        facts_status = "on" if orchestrator.is_facts_only(user_id) else "off"
        if dialog_memory is None:
            context_status = "off"
        else:
            context_status = "on" if await dialog_memory.is_enabled(user_id) else "off"
        reminders_status = "on" if settings.reminders_enabled else "off"
        modes = f"facts={facts_status}, context={context_status}, reminders={reminders_status}"
    return (
        "Health:\n"
        f"Uptime: {uptime}\n"
        f"Rate limits: {rate_limiter.per_minute}/min, {rate_limiter.per_day}/day\n"
        f"Python: {python_version}\n"
        f"Telegram: {telegram_version}\n"
        f"Orchestrator config: {settings.orchestrator_config_path}\n"
        f"Rate limit cache: {rate_limiter.cache_size} users\n"
        f"Modes: {modes}"
    )


@_with_error_handling
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context, bucket="ui"):
        return
    metadata = orchestrator.config.get("system_metadata", {})
    version = metadata.get("version", "unknown")
    now = datetime.now(timezone.utc).isoformat()
    result = _build_simple_result(
        f"pong (v{version}) {now}",
        intent="command.ping",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    available = orchestrator.list_tasks()
    if not available:
        result = _build_simple_result(
            "Нет доступных задач.",
            intent="command.tasks",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    lines = [f"• {task.name}: {task.description}" for task in available]
    result = _build_simple_result(
        "Доступные задачи:\n" + "\n".join(lines),
        intent="command.tasks",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    args = context.args
    if not args:
        result = _build_simple_result(
            "Укажите имя задачи и payload.",
            intent="command.task",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    if len(args) == 1:
        result = _build_simple_result(
            "Нужно передать payload. Пример: /task upper hello",
            intent="command.task",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return

    task_name = args[0]
    payload = " ".join(args[1:]).strip()
    if not payload:
        result = _build_simple_result(
            "Payload не может быть пустым.",
            intent="command.task",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return

    user_id = update.effective_user.id if update.effective_user else 0
    try:
        tool_result = orchestrator.execute_task(user_id, task_name, payload)
    except Exception as exc:
        set_status(context, "error")
        await _handle_exception(update, context, exc)
        return

    status = tool_result.status
    text = (
        "Результат:\n"
        f"Задача: {task_name}\n"
        f"Статус: {status}\n"
        f"Ответ: {tool_result.text}"
    )
    result = (
        ok(text, intent="command.task", mode="local")
        if status == "ok"
        else refused(text, intent="command.task", mode="local")
        if status == "refused"
        else error(text, intent="command.task", mode="local")
    )
    await send_result(update, context, result)


@_with_error_handling
async def last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    storage = _get_storage(context)
    user_id = update.effective_user.id if update.effective_user else 0
    record = storage.get_last_execution(user_id)
    if not record:
        result = _build_simple_result(
            "История пуста.",
            intent="command.last",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return

    result = _build_simple_result(
        "Последняя задача:\n"
        f"Дата: {record['timestamp']}\n"
        f"Задача: {record['task_name']}\n"
        f"Статус: {record['status']}\n"
        f"Payload: {record['payload']}\n"
        f"Ответ: {record['result']}",
        intent="command.last",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    prompt = " ".join(context.args).strip()
    if not prompt:
        result = _build_simple_result(
            "Введите текст запроса. Пример: /ask Привет",
            intent="command.ask",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    dialog_memory = _get_dialog_memory(context)
    if dialog_memory and await dialog_memory.is_enabled(user_id):
        await dialog_memory.add_user(user_id, chat_id, prompt)
    dialog_context, dialog_count = await _prepare_dialog_context(
        dialog_memory,
        user_id=user_id,
        chat_id=chat_id,
        prompt=prompt,
    )
    request_context = get_request_context(context)
    request_id = request_context.request_id if request_context else None
    try:
        result = await orchestrator.handle(
            f"/ask {prompt}",
            _build_user_context_with_dialog(
                update,
                dialog_context=dialog_context,
                dialog_message_count=dialog_count,
                request_id=request_id,
            ),
        )
    except Exception as exc:
        set_status(context, "error")
        await _handle_exception(update, context, exc)
        return
    await send_result(update, context, result)
    if dialog_memory and await dialog_memory.is_enabled(user_id) and _should_store_assistant_response(result):
        await dialog_memory.add_assistant(user_id, chat_id, result.text)


@_with_error_handling
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    prompt = " ".join(context.args).strip()
    if not prompt:
        result = _build_simple_result(
            "Введите текст запроса. Пример: /search Новости",
            intent="command.search",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    dialog_memory = _get_dialog_memory(context)
    if dialog_memory and await dialog_memory.is_enabled(user_id):
        await dialog_memory.add_user(user_id, chat_id, prompt)
    dialog_context, dialog_count = await _prepare_dialog_context(
        dialog_memory,
        user_id=user_id,
        chat_id=chat_id,
        prompt=prompt,
    )
    request_context = get_request_context(context)
    request_id = request_context.request_id if request_context else None
    try:
        result = await orchestrator.handle(
            f"/search {prompt}",
            _build_user_context_with_dialog(
                update,
                dialog_context=dialog_context,
                dialog_message_count=dialog_count,
                request_id=request_id,
            ),
        )
    except Exception as exc:
        set_status(context, "error")
        await _handle_exception(update, context, exc)
        return
    await send_result(update, context, result)
    if dialog_memory and await dialog_memory.is_enabled(user_id) and _should_store_assistant_response(result):
        await dialog_memory.add_assistant(user_id, chat_id, result.text)


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
    await send_result(update, context, result)


@_with_error_handling
async def facts_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    orchestrator.set_facts_only(user_id, True)
    result = _build_simple_result(
        "Режим фактов включён. Буду отвечать только с источниками.",
        intent="command.facts_on",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def facts_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    orchestrator.set_facts_only(user_id, False)
    result = _build_simple_result(
        "Режим фактов выключён. Можно отвечать без источников.",
        intent="command.facts_off",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def context_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    dialog_memory = _get_dialog_memory(context)
    if dialog_memory is None:
        result = _build_simple_result(
            "Контекст диалога не настроен.",
            intent="command.context_on",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    await dialog_memory.set_enabled(user_id, True)
    result = _build_simple_result(
        "Контекст диалога включён.",
        intent="command.context_on",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def context_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    dialog_memory = _get_dialog_memory(context)
    if dialog_memory is None:
        result = _build_simple_result(
            "Контекст диалога не настроен.",
            intent="command.context_off",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    await dialog_memory.set_enabled(user_id, False)
    result = _build_simple_result(
        "Контекст диалога выключён.",
        intent="command.context_off",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def context_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    dialog_memory = _get_dialog_memory(context)
    if dialog_memory is None:
        result = _build_simple_result(
            "Контекст диалога не настроен.",
            intent="command.context_clear",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    await dialog_memory.clear(user_id, chat_id)
    result = _build_simple_result(
        "История контекста очищена.",
        intent="command.context_clear",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def context_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    dialog_memory = _get_dialog_memory(context)
    if dialog_memory is None:
        result = _build_simple_result(
            "Контекст диалога не настроен.",
            intent="command.context_status",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    enabled, count = await dialog_memory.get_status(user_id, chat_id)
    status = "включён" if enabled else "выключён"
    result = _build_simple_result(
        f"Контекст {status}. user_id={user_id} chat_id={chat_id}. Сообщений в истории: {count}.",
        intent="command.context_status",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def allow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    if not context.args:
        result = _build_simple_result(
            "Укажите user_id. Пример: /allow 123456",
            intent="command.allow",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        result = _build_simple_result(
            "Некорректный user_id. Пример: /allow 123456",
            intent="command.allow",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    allowlist_store = _get_allowlist_store(context)
    added = await allowlist_store.add(target_id)
    admin_id = update.effective_user.id if update.effective_user else 0
    LOGGER.info("Allowlist update: admin_id=%s target_id=%s action=allow", admin_id, target_id)
    if added:
        result = _build_simple_result(
            f"Пользователь {target_id} добавлен в whitelist.",
            intent="command.allow",
            status="ok",
            mode="local",
        )
        await send_result(update, context, result)
    else:
        result = _build_simple_result(
            f"Пользователь {target_id} уже в whitelist.",
            intent="command.allow",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)


@_with_error_handling
async def deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    if not context.args:
        result = _build_simple_result(
            "Укажите user_id. Пример: /deny 123456",
            intent="command.deny",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        result = _build_simple_result(
            "Некорректный user_id. Пример: /deny 123456",
            intent="command.deny",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    allowlist_store = _get_allowlist_store(context)
    removed = await allowlist_store.remove(target_id)
    admin_id = update.effective_user.id if update.effective_user else 0
    LOGGER.info("Allowlist update: admin_id=%s target_id=%s action=deny", admin_id, target_id)
    if removed:
        result = _build_simple_result(
            f"Пользователь {target_id} удалён из whitelist.",
            intent="command.deny",
            status="ok",
            mode="local",
        )
        await send_result(update, context, result)
    else:
        result = _build_simple_result(
            f"Пользователь {target_id} не найден в whitelist.",
            intent="command.deny",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)


@_with_error_handling
async def allowlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    snapshot = _get_allowlist_store(context).snapshot()
    if not snapshot.allowed_user_ids:
        result = _build_simple_result(
            "Whitelist пуст.",
            intent="command.allowlist",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    lines = [str(user_id) for user_id in snapshot.allowed_user_ids]
    message = "Whitelist пользователей:\n" + "\n".join(lines) + f"\n\nВсего: {len(lines)}"
    result = _build_simple_result(
        message,
        intent="command.allowlist",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context, bucket="ui"):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    result = ok(
        "Меню:",
        intent="command.menu",
        mode="local",
        actions=_build_menu_actions(context, user_id=user_id),
    )
    await send_result(update, context, result)


@_with_error_handling
async def action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    if not await _guard_access(update, context, bucket="ui"):
        return
    await query.answer()
    data = query.data or ""
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    token = parse_callback_token(data)
    if token is None:
        result = refused(
            "Действие недоступно.",
            intent="ui.action",
            mode="local",
            debug={"reason": "invalid_callback"},
        )
        await send_result(update, context, result)
        return
    store = _get_action_store(context)
    stored = store.pop_action(user_id=user_id, chat_id=chat_id, token=token)
    if stored is None:
        result = refused(
            "Кнопка устарела, открой меню заново.",
            intent="ui.action",
            mode="local",
            debug={"reason": "action_missing"},
        )
        await send_result(update, context, result)
        return
    result = await _dispatch_action(update, context, stored)
    await send_result(update, context, result)


async def _dispatch_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    stored: StoredAction,
) -> OrchestratorResult:
    orchestrator = _get_orchestrator(context)
    user_id = update.effective_user.id if update.effective_user else 0
    payload = stored.payload
    op = payload.get("op")
    if op == "menu_open":
        user_id = update.effective_user.id if update.effective_user else 0
        return ok("Меню:", intent="menu.open", mode="local", actions=_build_menu_actions(context, user_id=user_id))
    if op == "run_command":
        command = payload.get("command")
        args = payload.get("args", "")
        if not isinstance(command, str):
            return error(
                "Некорректные данные действия.",
                intent="ui.action",
                mode="local",
                debug={"reason": "invalid_command"},
            )
        return await _dispatch_command_payload(
            update,
            context,
            command=command,
            args=args if isinstance(args, str) else "",
        )
    if op == "reminder_off":
        reminder_id = payload.get("id")
        if not isinstance(reminder_id, str) or not reminder_id:
            return error(
                "Некорректные данные действия.",
                intent="ui.action",
                mode="local",
                debug={"reason": "invalid_reminder_id"},
            )
        return await _handle_reminder_off(context, user_id=user_id, reminder_id=reminder_id)
    if op == "reminder_on":
        event_id = payload.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            return error(
                "Некорректные данные действия.",
                intent="ui.action",
                mode="local",
                debug={"reason": "invalid_event_id"},
            )
        return await _handle_reminder_on(context, user_id=user_id, event_id=event_id)
    if stored.intent == "task.execute":
        task_name = payload.get("name")
        task_payload = payload.get("payload")
        if not isinstance(task_name, str) or not isinstance(task_payload, str):
            return error(
                "Некорректные данные действия.",
                intent="ui.action",
                mode="local",
                debug={"reason": "invalid_task_payload"},
            )
        return orchestrator.execute_task(user_id, task_name, task_payload)
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        return await orchestrator.handle(text, _build_user_context(update))
    return refused(
        "Действие не поддерживается.",
        intent="ui.action",
        mode="local",
        debug={"reason": "unknown_action", "action_id": stored.intent},
    )


async def _dispatch_command_payload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    command: str,
    args: str,
) -> OrchestratorResult:
    normalized = routing.normalize_command(command)
    orchestrator = _get_orchestrator(context)
    if normalized in {"/menu", "/start"}:
        user_id = update.effective_user.id if update.effective_user else 0
        return ok("Меню:", intent="menu.open", mode="local", actions=_build_menu_actions(context, user_id=user_id))
    if normalized == "/calc":
        return ok("Calc: /calc <выражение>.", intent="menu.calc", mode="local")
    if normalized == "/calendar":
        return ok(
            "Calendar: /calendar add YYYY-MM-DD HH:MM [-m MINUTES] <title> | list [YYYY-MM-DD YYYY-MM-DD] | today | week | del <id> | debug_due.",
            intent="menu.calendar",
            mode="local",
        )
    if normalized == "/check":
        return ok("Проверка: /check <текст> или ответом на сообщение.", intent="menu.check", mode="local")
    if normalized == "/help":
        access_note = ""
        if orchestrator.is_access_restricted():
            access_note = "\n\nДоступ ограничен whitelist пользователей."
        return ok(_build_help_text(access_note), intent="menu.help", mode="local")
    if normalized == "/health":
        user_id = update.effective_user.id if update.effective_user else 0
        message = await _build_health_message(context, user_id=user_id)
        return ok(message, intent="menu.status", mode="local")
    if normalized == "/summary":
        return ok(
            "Summary: /summary <текст> или summary: <текст>.",
            intent="menu.summary",
            mode="local",
        )
    if normalized == "/reminders":
        now = datetime.now(tz=calendar_store.VIENNA_TZ)
        return await list_reminders(now, limit=5, intent="menu.reminders")
    if normalized in {"/facts_on", "/facts_off"}:
        user_id = update.effective_user.id if update.effective_user else 0
        enabled = normalized == "/facts_on"
        orchestrator.set_facts_only(user_id, enabled)
        text = (
            "Режим фактов включён. Буду отвечать только с источниками."
            if enabled
            else "Режим фактов выключён. Можно отвечать без источников."
        )
        return ok(text, intent="menu.facts", mode="local")
    return refused(
        f"Команда недоступна: {command}",
        intent="ui.action",
        mode="local",
        debug={"command": command, "args": args},
    )


async def _handle_reminder_off(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    reminder_id: str,
) -> OrchestratorResult:
    scheduler = _get_reminder_scheduler(context)
    if scheduler:
        try:
            await scheduler.cancel_reminder(reminder_id)
        except Exception:
            LOGGER.exception("Failed to cancel reminder: reminder_id=%s", reminder_id)
            return error(
                "Не удалось отменить напоминание.",
                intent="utility_reminders.off",
                mode="local",
            )
    else:
        await calendar_store.disable_reminder(reminder_id)
    return ok(
        f"Напоминание отключено: {reminder_id}",
        intent="utility_reminders.off",
        mode="local",
    )


async def _handle_reminder_on(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    event_id: str,
) -> OrchestratorResult:
    event = await calendar_store.get_event(event_id)
    if event is None:
        return refused(
            f"Событие не найдено: {event_id}",
            intent="utility_reminders.on",
            mode="local",
        )
    settings = context.application.bot_data.get("settings")
    offset_minutes = settings.reminder_default_offset_minutes if settings is not None else 10
    trigger_at = event.dt - timedelta(minutes=offset_minutes) if offset_minutes else event.dt
    scheduler = _get_reminder_scheduler(context)
    reminder = await calendar_store.ensure_reminder_for_event(event, trigger_at, enabled=True)
    if scheduler and settings is not None and settings.reminders_enabled:
        try:
            await scheduler.schedule_reminder(reminder)
        except Exception:
            LOGGER.exception("Failed to schedule reminder: reminder_id=%s", reminder.id)
            return error(
                "Не удалось включить напоминание.",
                intent="utility_reminders.on",
                mode="local",
            )
    return ok(
        f"Напоминание включено: {reminder.id}",
        intent="utility_reminders.on",
        mode="local",
    )


def _extract_text_from_image(image_bytes: bytes) -> str:
    with Image.open(io.BytesIO(image_bytes)) as image:
        return pytesseract.image_to_string(image).strip()


@_with_error_handling
async def image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    prompt = " ".join(context.args).strip()
    if not prompt:
        result = _build_simple_result(
            "Укажите описание изображения. Пример: /image Слон в космосе",
            intent="command.image",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    openai_client = _get_openai_client(context)
    if openai_client is None or not openai_client.api_key:
        result = _build_simple_result(
            "Генерация изображений не настроена.",
            intent="command.image",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    response = await openai_client.create_image(prompt=prompt)
    data = response.get("data") or []
    image_url = data[0].get("url") if data else None
    if not image_url:
        result = _build_simple_result(
            "Не удалось получить изображение.",
            intent="command.image",
            status="error",
            mode="local",
        )
        await send_result(update, context, result)
        return
    result = ok(
        "Готово! Сгенерированное изображение ниже.",
        intent="command.image",
        mode="tool",
        attachments=[{"type": "image", "name": "generated", "url": image_url}],
    )
    await send_result(update, context, result)


@_with_error_handling
async def check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    prompt = " ".join(context.args).strip()
    if not prompt and update.message and update.message.reply_to_message:
        prompt = (update.message.reply_to_message.text or "").strip()
    if not prompt:
        result = _build_simple_result(
            "Использование: /check <текст> или ответом на сообщение.",
            intent="command.check",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    result = await llm_check(prompt, _build_tool_context(update, context))
    await send_result(update, context, result)


@_with_error_handling
async def rewrite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    if not context.args or len(context.args) < 2:
        result = _build_simple_result(
            "Использование: /rewrite <simple|hard|short> <текст>.",
            intent="command.rewrite",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    mode = context.args[0].lower()
    if mode not in {"simple", "hard", "short"}:
        result = _build_simple_result(
            "Некорректный режим. Использование: /rewrite <simple|hard|short> <текст>.",
            intent="command.rewrite",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    prompt = " ".join(context.args[1:]).strip()
    if not prompt:
        result = _build_simple_result(
            "Введите текст для переписывания. Пример: /rewrite simple текст.",
            intent="command.rewrite",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    result = await llm_rewrite(mode, prompt, _build_tool_context(update, context))
    await send_result(update, context, result)


@_with_error_handling
async def explain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    prompt = " ".join(context.args).strip()
    if not prompt and update.message and update.message.reply_to_message:
        prompt = (update.message.reply_to_message.text or "").strip()
    if not prompt:
        result = _build_simple_result(
            "Использование: /explain <текст> или ответом на сообщение.",
            intent="command.explain",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    result = await llm_explain(prompt, _build_tool_context(update, context))
    await send_result(update, context, result)


@_with_error_handling
async def calc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    expression = " ".join(context.args).strip()
    if not expression:
        result = _build_simple_result(
            "Использование: /calc <выражение>.",
            intent="utility_calc",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    try:
        result_value = parse_and_eval(expression)
    except CalcError as exc:
        result = error(f"Ошибка вычисления: {exc}", intent="utility_calc", mode="local")
        await send_result(update, context, result)
        return
    result = ok(f"{expression} = {result_value}", intent="utility_calc", mode="local")
    await send_result(update, context, result)


@_with_error_handling
async def calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    args = context.args
    if not args:
        result = refused(
            "Использование: /calendar add YYYY-MM-DD HH:MM [-m MINUTES] <title> | list [YYYY-MM-DD YYYY-MM-DD] | "
            "today | week | del <id> | debug_due.",
            intent="utility_calendar",
            mode="local",
        )
        await send_result(update, context, result)
        return
    command = args[0].lower()
    if command == "add":
        if len(args) < 4:
            result = refused(
                "Использование: /calendar add YYYY-MM-DD HH:MM [-m MINUTES] <title>.",
                intent="utility_calendar.add",
                mode="local",
            )
            await send_result(update, context, result)
            return
        date_part = args[1]
        time_part = args[2]
        minutes_before = None
        title_start = 3
        if len(args) >= 5 and args[3] == "-m":
            try:
                minutes_before = int(args[4])
            except ValueError:
                result = refused(
                    "Минуты должны быть числом. Пример: /calendar add 2026-02-03 18:30 -m 10 Позвонить маме.",
                    intent="utility_calendar.add",
                    mode="local",
                )
                await send_result(update, context, result)
                return
            if minutes_before < 0:
                result = refused(
                    "Минуты не могут быть отрицательными.",
                    intent="utility_calendar.add",
                    mode="local",
                )
                await send_result(update, context, result)
                return
            title_start = 5
        settings = context.application.bot_data.get("settings")
        if minutes_before is None and settings is not None:
            minutes_before = settings.reminder_default_offset_minutes
        title = " ".join(args[title_start:]).strip()
        if not title:
            result = refused(
                "Укажите название события. Пример: /calendar add 2026-02-05 18:30 Врач.",
                intent="utility_calendar.add",
                mode="local",
            )
            await send_result(update, context, result)
            return
        try:
            dt = calendar_store.parse_local_datetime(f"{date_part} {time_part}")
        except ValueError:
            result = refused(
                "Неверный формат даты. Пример: /calendar add 2026-02-05 18:30 Врач.",
                intent="utility_calendar.add",
                mode="local",
            )
            await send_result(update, context, result)
            return
        remind_at = dt
        if minutes_before:
            remind_at = dt - timedelta(minutes=minutes_before)
        chat_id = update.effective_chat.id if update.effective_chat else 0
        reminders_enabled = True
        if settings is not None and not settings.reminders_enabled:
            reminders_enabled = False
        result_item = await calendar_store.add_item(
            dt,
            title,
            chat_id=chat_id,
            remind_at=remind_at,
            user_id=user_id,
            reminders_enabled=reminders_enabled,
        )
        event = result_item["event"]
        reminder = result_item["reminder"]
        scheduler = _get_reminder_scheduler(context)
        if scheduler and reminders_enabled:
            try:
                reminder_item = await calendar_store.get_reminder(reminder["reminder_id"])
                if reminder_item:
                    await scheduler.schedule_reminder(reminder_item)
            except Exception:
                LOGGER.exception("Failed to schedule reminder: reminder_id=%s", reminder.get("reminder_id"))
        dt_label = dt.strftime("%Y-%m-%d %H:%M")
        text = f"Добавлено: {event['event_id']} | {dt_label} | {title}"
        result = ok(text, intent="utility_calendar.add", mode="local")
        await send_result(update, context, result)
        return
    if command == "list":
        start = end = None
        if len(args) == 3:
            try:
                start_date = calendar_store.parse_date(args[1])
                end_date = calendar_store.parse_date(args[2])
            except ValueError:
                result = refused(
                    "Неверный формат. Пример: /calendar list 2026-02-01 2026-02-28.",
                    intent="utility_calendar.list",
                    mode="local",
                )
                await send_result(update, context, result)
                return
            start, _ = calendar_store.day_bounds(start_date)
            _, end = calendar_store.day_bounds(end_date)
        elif len(args) != 1:
            result = refused(
                "Использование: /calendar list [YYYY-MM-DD YYYY-MM-DD].",
                intent="utility_calendar.list",
                mode="local",
            )
            await send_result(update, context, result)
            return
        tool_result = await list_calendar_items(start, end, intent="utility_calendar.list")
        result = tool_result
        await send_result(update, context, result)
        return
    if command == "today":
        today = datetime.now(tz=calendar_store.VIENNA_TZ).date()
        start, end = calendar_store.day_bounds(today)
        result = await list_calendar_items(start, end, intent="utility_calendar.today")
        await send_result(update, context, result)
        return
    if command == "week":
        today = datetime.now(tz=calendar_store.VIENNA_TZ).date()
        start, end = calendar_store.week_bounds(today)
        result = await list_calendar_items(start, end, intent="utility_calendar.week")
        await send_result(update, context, result)
        return
    if command == "del":
        if len(args) < 2:
            result = refused(
                "Использование: /calendar del <id>.",
                intent="utility_calendar.del",
                mode="local",
            )
            await send_result(update, context, result)
            return
        item_id = args[1].strip()
        if not item_id:
            result = refused("Укажите id для удаления.", intent="utility_calendar.del", mode="local")
            await send_result(update, context, result)
            return
        removed, reminder_id = await calendar_store.delete_item(item_id)
        scheduler = _get_reminder_scheduler(context)
        if reminder_id and scheduler:
            try:
                await scheduler.cancel_reminder(reminder_id)
            except Exception:
                LOGGER.exception("Failed to cancel reminder: reminder_id=%s", reminder_id)
        if removed:
            text = f"Удалено: {item_id}"
            status = "ok"
        else:
            text = f"Не найдено: {item_id}"
            status = "refused"
        result = (
            ok(text, intent="utility_calendar.del", mode="local")
            if status == "ok"
            else refused(text, intent="utility_calendar.del", mode="local")
        )
        await send_result(update, context, result)
        return
    if command == "debug_due":
        now = datetime.now(tz=calendar_store.VIENNA_TZ)
        due_items = await calendar_store.list_due_reminders(now, limit=5)
        if not due_items:
            result = ok("Нет просроченных напоминаний.", intent="utility_calendar.debug_due", mode="local")
            await send_result(update, context, result)
            return
        lines = []
        for item in due_items:
            remind_label = item.trigger_at.astimezone(calendar_store.VIENNA_TZ).strftime("%Y-%m-%d %H:%M")
            lines.append(
                f"{item.id} | trigger_at={remind_label} | enabled={item.enabled} | {item.text}"
            )
        result = ok("\n".join(lines), intent="utility_calendar.debug_due", mode="local")
        await send_result(update, context, result)
        return
    result = refused(
        "Неизвестная команда. Использование: /calendar add|list|today|week|del|debug_due.",
        intent="utility_calendar",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    message_text = update.message.text if update.message else ""
    if message_text and routing.normalize_command(message_text) != "/reminders":
        user_id = update.effective_user.id if update.effective_user else 0
        LOGGER.warning(
            "Reminders route guard: user_id=%s command=%s fallback=llm",
            user_id,
            routing.normalize_command(message_text) or "-",
        )
        await chat(update, context)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    limit = 5
    if context.args:
        try:
            limit = max(1, int(context.args[0]))
        except ValueError:
            result = refused("Использование: /reminders [N].", intent="utility_reminders.list", mode="local")
            await send_result(update, context, result)
            return
    now = datetime.now(tz=calendar_store.VIENNA_TZ)
    result = await list_reminders(now, limit=limit, intent="utility_reminders.list")
    await send_result(update, context, result)


@_with_error_handling
async def reminder_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    if not context.args:
        result = refused("Использование: /reminder_off <id>.", intent="utility_reminders.off", mode="local")
        await send_result(update, context, result)
        return
    reminder_id = context.args[0].strip()
    if not reminder_id:
        result = refused("Укажите id напоминания.", intent="utility_reminders.off", mode="local")
        await send_result(update, context, result)
        return
    result = await _handle_reminder_off(context, user_id=user_id, reminder_id=reminder_id)
    await send_result(update, context, result)


@_with_error_handling
async def reminder_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    if not context.args:
        result = refused(
            "Использование: /reminder_on <event_id>.",
            intent="utility_reminders.on",
            mode="local",
        )
        await send_result(update, context, result)
        return
    event_id = context.args[0].strip()
    if not event_id:
        result = refused("Укажите event_id.", intent="utility_reminders.on", mode="local")
        await send_result(update, context, result)
        return
    result = await _handle_reminder_on(context, user_id=user_id, event_id=event_id)
    await send_result(update, context, result)


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
        result = _build_simple_result(
            "Не удалось распознать текст.",
            intent="utility_ocr",
            status="error",
            mode="local",
        )
        await send_result(update, context, result)
        return
    if not text:
        result = _build_simple_result(
            "Текст не найден.",
            intent="utility_ocr",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    result = _build_simple_result(text, intent="utility_ocr", status="ok", mode="local")
    await send_result(update, context, result)


@_with_error_handling
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    message = update.message.text if update.message else ""
    prompt = message.strip()
    if not prompt:
        return
    if menu.is_menu_label(prompt):
        result = refused(
            "Используй /menu и нажимай кнопки, или введи команду /calc ...",
            intent="guard.menu_label",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    LOGGER.info("chat_ids user_id=%s chat_id=%s has_message=%s", user_id, chat_id, bool(update.message))
    dialog_memory = _get_dialog_memory(context)
    if user_id == 0 or chat_id == 0:
        LOGGER.warning("memory_skip_missing_ids user_id=%s chat_id=%s", user_id, chat_id)
        dialog_memory = None
    elif dialog_memory and await dialog_memory.is_enabled(user_id):
        await dialog_memory.add_user(user_id, chat_id, prompt)
        LOGGER.info("memory_wrote user_id=%s chat_id=%s", user_id, chat_id)
    dialog_context, dialog_count = await _prepare_dialog_context(
        dialog_memory,
        user_id=user_id,
        chat_id=chat_id,
        prompt=prompt,
    )
    request_context = get_request_context(context)
    request_id = request_context.request_id if request_context else None
    try:
        result = await orchestrator.handle(
            prompt,
            _build_user_context_with_dialog(
                update,
                dialog_context=dialog_context,
                dialog_message_count=dialog_count,
                request_id=request_id,
            ),
        )
    except Exception as exc:
        set_status(context, "error")
        await _handle_exception(update, context, exc)
        return
    await send_result(update, context, result)
    if dialog_memory and await dialog_memory.is_enabled(user_id) and _should_store_assistant_response(result):
        await dialog_memory.add_assistant(user_id, chat_id, result.text)


@_with_error_handling
async def selfcheck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    settings = context.application.bot_data["settings"]
    allowlist_snapshot = _get_allowlist_store(context).snapshot()
    allowed_user_ids = allowlist_snapshot.allowed_user_ids
    if allowed_user_ids:
        allowed_summary = f"ok ({len(allowed_user_ids)}): {', '.join(map(str, allowed_user_ids))}"
    else:
        allowed_summary = "empty (доступ закрыт)"
    message = (
        "Self-check:\n"
        f"ALLOWLIST_PATH: {settings.allowlist_path}\n"
        f"ALLOWLIST_USERS: {allowed_summary}\n"
        f"RATE_LIMIT_PER_MINUTE: {settings.rate_limit_per_minute}\n"
        f"RATE_LIMIT_PER_DAY: {settings.rate_limit_per_day}\n"
        f"HISTORY_SIZE: {settings.history_size}\n"
        f"TELEGRAM_MESSAGE_LIMIT: {settings.telegram_message_limit}"
    )
    result = _build_simple_result(message, intent="command.selfcheck", status="ok", mode="local")
    await send_result(update, context, result)


@_with_error_handling
async def health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context, bucket="ui"):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    result = _build_simple_result(
        await _build_health_message(context, user_id=user_id),
        intent="command.health",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_status(context, "error")
    LOGGER.exception("Unhandled exception", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        result = _build_simple_result(
            "Ошибка на сервере. Попробуй ещё раз.",
            intent="error",
            status="error",
            mode="local",
        )
        await send_result(update, context, result)
