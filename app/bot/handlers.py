from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from functools import wraps

import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from PIL import Image
import pytesseract

from app.bot import menu
from app.core import calendar_store
from app.core.calc import CalcError, parse_and_eval
from app.core.dialog_memory import DialogMemory, DialogMessage
from app.core.orchestrator import Orchestrator
from app.core.result import Action, OrchestratorResult, ensure_valid, error, ok, refused
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


def _get_rate_limiter(context: ContextTypes.DEFAULT_TYPE) -> RateLimiter:
    return context.application.bot_data["rate_limiter"]


def _get_history(context: ContextTypes.DEFAULT_TYPE) -> dict[int, list[tuple[datetime, str, str]]]:
    return context.application.bot_data["history"]


def _get_dialog_memory(context: ContextTypes.DEFAULT_TYPE) -> DialogMemory | None:
    return context.application.bot_data.get("dialog_memory")


def _get_openai_client(context: ContextTypes.DEFAULT_TYPE) -> OpenAIClient | None:
    return context.application.bot_data.get("openai_client")


def _get_reminder_scheduler(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data.get("reminder_scheduler")


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
    await safe_send_text(update, context, f"Доступ запрещён.\nТвой user_id: {user_id}")


async def _require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id if update.effective_user else 0
    if _is_admin(context, user_id):
        return True
    set_status(context, "error")
    await safe_send_text(update, context, "Недостаточно прав.")
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
    await safe_send_text(update, context, response)


def _build_user_context(update: Update) -> dict[str, int]:
    user_id = update.effective_user.id if update.effective_user else 0
    return {"user_id": user_id}


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
    if result.debug:
        LOGGER.info("Orchestrator debug: user_id=%s intent=%s debug=%s", user_id, result.intent, result.debug)


def _encode_action_data(action: Action) -> str:
    payload = {"id": action.id, "payload": action.payload}
    data = json.dumps(payload, ensure_ascii=False)
    if len(data) > 60:
        return f"action:{action.id}"
    return data


def _build_action_keyboard(actions: list[Action]) -> InlineKeyboardMarkup | None:
    if not actions:
        return None
    buttons = [[InlineKeyboardButton(action.label, callback_data=_encode_action_data(action))] for action in actions]
    return InlineKeyboardMarkup(buttons)


async def _send_result(update: Update, context: ContextTypes.DEFAULT_TYPE, result: OrchestratorResult) -> None:
    public_result = ensure_valid(result)
    reply_markup = _build_action_keyboard(public_result.actions)
    await safe_send_text(update, context, public_result.text, reply_markup=reply_markup)


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
    await menu.show_menu(update, context, message + access_note)


@_with_error_handling
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    access_note = ""
    if orchestrator.is_access_restricted():
        access_note = "\n\nДоступ ограничен whitelist пользователей."
    await safe_send_text(update, context, _build_help_text(access_note))


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


def _build_health_message(context: ContextTypes.DEFAULT_TYPE) -> str:
    settings = context.application.bot_data["settings"]
    rate_limiter = _get_rate_limiter(context)
    start_time = context.application.bot_data.get("start_time", time.monotonic())
    uptime = _format_uptime(start_time)
    python_version = sys.version.split()[0]
    telegram_version = telegram.__version__
    return (
        "Health:\n"
        f"Uptime: {uptime}\n"
        f"Rate limits: {rate_limiter.per_minute}/min, {rate_limiter.per_day}/day\n"
        f"Python: {python_version}\n"
        f"Telegram: {telegram_version}\n"
        f"Orchestrator config: {settings.orchestrator_config_path}\n"
        f"Rate limit cache: {rate_limiter.cache_size} users"
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
    _log_orchestrator_result(user_id, payload, result)
    await _send_result(update, context, result)


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
    _log_orchestrator_result(user_id, prompt, result)
    await _send_result(update, context, result)
    if dialog_memory and await dialog_memory.is_enabled(user_id) and _should_store_assistant_response(result):
        await dialog_memory.add_assistant(user_id, chat_id, result.text)


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
    _log_orchestrator_result(user_id, prompt, result)
    await _send_result(update, context, result)
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
    _log_orchestrator_result(user_id, prompt, result)
    await _send_result(update, context, result)


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


@_with_error_handling
async def context_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    dialog_memory = _get_dialog_memory(context)
    if dialog_memory is None:
        await safe_send_text(update, context, "Контекст диалога не настроен.")
        return
    user_id = update.effective_user.id if update.effective_user else 0
    await dialog_memory.set_enabled(user_id, True)
    await safe_send_text(update, context, "Контекст диалога включён.")


@_with_error_handling
async def context_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    dialog_memory = _get_dialog_memory(context)
    if dialog_memory is None:
        await safe_send_text(update, context, "Контекст диалога не настроен.")
        return
    user_id = update.effective_user.id if update.effective_user else 0
    await dialog_memory.set_enabled(user_id, False)
    await safe_send_text(update, context, "Контекст диалога выключён.")


@_with_error_handling
async def context_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    dialog_memory = _get_dialog_memory(context)
    if dialog_memory is None:
        await safe_send_text(update, context, "Контекст диалога не настроен.")
        return
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    await dialog_memory.clear(user_id, chat_id)
    await safe_send_text(update, context, "История контекста очищена.")


@_with_error_handling
async def context_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    dialog_memory = _get_dialog_memory(context)
    if dialog_memory is None:
        await safe_send_text(update, context, "Контекст диалога не настроен.")
        return
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    enabled, count = await dialog_memory.get_status(user_id, chat_id)
    status = "включён" if enabled else "выключён"
    await safe_send_text(
        update,
        context,
        f"Контекст {status}. user_id={user_id} chat_id={chat_id}. Сообщений в истории: {count}.",
    )


@_with_error_handling
async def allow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    if not context.args:
        await safe_send_text(update, context, "Укажите user_id. Пример: /allow 123456")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await safe_send_text(update, context, "Некорректный user_id. Пример: /allow 123456")
        return
    allowlist_store = _get_allowlist_store(context)
    added = await allowlist_store.add(target_id)
    admin_id = update.effective_user.id if update.effective_user else 0
    LOGGER.info("Allowlist update: admin_id=%s target_id=%s action=allow", admin_id, target_id)
    if added:
        await safe_send_text(update, context, f"Пользователь {target_id} добавлен в whitelist.")
    else:
        await safe_send_text(update, context, f"Пользователь {target_id} уже в whitelist.")


@_with_error_handling
async def deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    if not context.args:
        await safe_send_text(update, context, "Укажите user_id. Пример: /deny 123456")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await safe_send_text(update, context, "Некорректный user_id. Пример: /deny 123456")
        return
    allowlist_store = _get_allowlist_store(context)
    removed = await allowlist_store.remove(target_id)
    admin_id = update.effective_user.id if update.effective_user else 0
    LOGGER.info("Allowlist update: admin_id=%s target_id=%s action=deny", admin_id, target_id)
    if removed:
        await safe_send_text(update, context, f"Пользователь {target_id} удалён из whitelist.")
    else:
        await safe_send_text(update, context, f"Пользователь {target_id} не найден в whitelist.")


@_with_error_handling
async def allowlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    snapshot = _get_allowlist_store(context).snapshot()
    if not snapshot.allowed_user_ids:
        await safe_send_text(update, context, "Whitelist пуст.")
        return
    lines = [str(user_id) for user_id in snapshot.allowed_user_ids]
    message = "Whitelist пользователей:\n" + "\n".join(lines) + f"\n\nВсего: {len(lines)}"
    await safe_send_text(update, context, message)


@_with_error_handling
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    await menu.show_menu(update, context)


@_with_error_handling
async def menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text not in {
        menu.STATUS_BUTTON,
        menu.SUMMARY_BUTTON,
        menu.FACTS_TOGGLE_BUTTON,
        menu.HELP_BUTTON,
        menu.CHECK_BUTTON,
        menu.REWRITE_BUTTON,
        menu.EXPLAIN_BUTTON,
        menu.CALC_BUTTON,
        menu.CALENDAR_BUTTON,
    }:
        return
    if not await _guard_access(update, context):
        return
    orchestrator = _get_orchestrator(context)
    if text == menu.STATUS_BUTTON:
        await safe_send_text(update, context, _build_health_message(context))
        return
    if text == menu.SUMMARY_BUTTON:
        await safe_send_text(update, context, "Суммаризация: summary: <текст> или /summary <текст>.")
        return
    if text == menu.FACTS_TOGGLE_BUTTON:
        user_id = update.effective_user.id if update.effective_user else 0
        new_value = not orchestrator.is_facts_only(user_id)
        orchestrator.set_facts_only(user_id, new_value)
        status = "включён" if new_value else "выключён"
        await safe_send_text(update, context, f"Режим фактов {status}.")
        return
    if text == menu.HELP_BUTTON:
        access_note = ""
        if orchestrator.is_access_restricted():
            access_note = "\n\nДоступ ограничен whitelist пользователей."
        await safe_send_text(update, context, _build_help_text(access_note))
        return
    if text == menu.CHECK_BUTTON:
        await safe_send_text(update, context, "Проверка: /check <текст> или ответом на сообщение.")
        return
    if text == menu.REWRITE_BUTTON:
        await safe_send_text(update, context, "Rewrite: /rewrite <simple|hard|short> <текст>.")
        return
    if text == menu.EXPLAIN_BUTTON:
        await safe_send_text(update, context, "Explain: /explain <текст> или ответом на сообщение.")
        return
    if text == menu.CALC_BUTTON:
        await safe_send_text(update, context, "Calc: /calc <выражение>.")
        return
    if text == menu.CALENDAR_BUTTON:
        await safe_send_text(
            update,
            context,
            "Calendar: /calendar add YYYY-MM-DD HH:MM [-m MINUTES] <title> | list [YYYY-MM-DD YYYY-MM-DD] | today | week | del <id> | debug_due.",
        )
        return


@_with_error_handling
async def action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data or ""
    user_id = update.effective_user.id if update.effective_user else 0
    result = refused(
        "Действие недоступно.",
        intent="ui.action",
        mode="local",
        debug={"callback_data": data},
    )
    _log_orchestrator_result(user_id, data, result)
    await _send_result(update, context, result)


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
async def check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    prompt = " ".join(context.args).strip()
    if not prompt and update.message and update.message.reply_to_message:
        prompt = (update.message.reply_to_message.text or "").strip()
    if not prompt:
        await safe_send_text(
            update,
            context,
            "Использование: /check <текст> или ответом на сообщение.",
        )
        return
    user_id = update.effective_user.id if update.effective_user else 0
    result = await llm_check(prompt, _build_tool_context(update, context))
    _log_orchestrator_result(user_id, prompt, result)
    await _send_result(update, context, result)


@_with_error_handling
async def rewrite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    if not context.args or len(context.args) < 2:
        await safe_send_text(
            update,
            context,
            "Использование: /rewrite <simple|hard|short> <текст>.",
        )
        return
    mode = context.args[0].lower()
    if mode not in {"simple", "hard", "short"}:
        await safe_send_text(
            update,
            context,
            "Некорректный режим. Использование: /rewrite <simple|hard|short> <текст>.",
        )
        return
    prompt = " ".join(context.args[1:]).strip()
    if not prompt:
        await safe_send_text(
            update,
            context,
            "Введите текст для переписывания. Пример: /rewrite simple текст.",
        )
        return
    user_id = update.effective_user.id if update.effective_user else 0
    result = await llm_rewrite(mode, prompt, _build_tool_context(update, context))
    _log_orchestrator_result(user_id, prompt, result)
    await _send_result(update, context, result)


@_with_error_handling
async def explain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    prompt = " ".join(context.args).strip()
    if not prompt and update.message and update.message.reply_to_message:
        prompt = (update.message.reply_to_message.text or "").strip()
    if not prompt:
        await safe_send_text(
            update,
            context,
            "Использование: /explain <текст> или ответом на сообщение.",
        )
        return
    user_id = update.effective_user.id if update.effective_user else 0
    result = await llm_explain(prompt, _build_tool_context(update, context))
    _log_orchestrator_result(user_id, prompt, result)
    await _send_result(update, context, result)


@_with_error_handling
async def calc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    expression = " ".join(context.args).strip()
    if not expression:
        await safe_send_text(update, context, "Использование: /calc <выражение>.")
        return
    user_id = update.effective_user.id if update.effective_user else 0
    try:
        result_value = parse_and_eval(expression)
    except CalcError as exc:
        result = error(f"Ошибка вычисления: {exc}", intent="utility_calc", mode="local")
        _log_orchestrator_result(user_id, expression, result)
        await _send_result(update, context, result)
        return
    result = ok(f"{expression} = {result_value}", intent="utility_calc", mode="local")
    _log_orchestrator_result(user_id, expression, result)
    await _send_result(update, context, result)


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
        _log_orchestrator_result(user_id, "", result)
        await _send_result(update, context, result)
        return
    command = args[0].lower()
    if command == "add":
        if len(args) < 4:
            result = refused(
                "Использование: /calendar add YYYY-MM-DD HH:MM [-m MINUTES] <title>.",
                intent="utility_calendar.add",
                mode="local",
            )
            _log_orchestrator_result(user_id, " ".join(args), result)
            await _send_result(update, context, result)
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
                _log_orchestrator_result(user_id, " ".join(args), result)
                await _send_result(update, context, result)
                return
            if minutes_before < 0:
                result = refused(
                    "Минуты не могут быть отрицательными.",
                    intent="utility_calendar.add",
                    mode="local",
                )
                _log_orchestrator_result(user_id, " ".join(args), result)
                await _send_result(update, context, result)
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
            _log_orchestrator_result(user_id, " ".join(args), result)
            await _send_result(update, context, result)
            return
        try:
            dt = calendar_store.parse_local_datetime(f"{date_part} {time_part}")
        except ValueError:
            result = refused(
                "Неверный формат даты. Пример: /calendar add 2026-02-05 18:30 Врач.",
                intent="utility_calendar.add",
                mode="local",
            )
            _log_orchestrator_result(user_id, " ".join(args), result)
            await _send_result(update, context, result)
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
        _log_orchestrator_result(user_id, " ".join(args), result)
        await _send_result(update, context, result)
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
                _log_orchestrator_result(user_id, " ".join(args), result)
                await _send_result(update, context, result)
                return
            start, _ = calendar_store.day_bounds(start_date)
            _, end = calendar_store.day_bounds(end_date)
        elif len(args) != 1:
            result = refused(
                "Использование: /calendar list [YYYY-MM-DD YYYY-MM-DD].",
                intent="utility_calendar.list",
                mode="local",
            )
            _log_orchestrator_result(user_id, " ".join(args), result)
            await _send_result(update, context, result)
            return
        tool_result = await list_calendar_items(start, end, intent="utility_calendar.list")
        result = tool_result
        _log_orchestrator_result(user_id, " ".join(args), result)
        await _send_result(update, context, result)
        return
    if command == "today":
        today = datetime.now(tz=calendar_store.VIENNA_TZ).date()
        start, end = calendar_store.day_bounds(today)
        result = await list_calendar_items(start, end, intent="utility_calendar.today")
        _log_orchestrator_result(user_id, " ".join(args), result)
        await _send_result(update, context, result)
        return
    if command == "week":
        today = datetime.now(tz=calendar_store.VIENNA_TZ).date()
        start, end = calendar_store.week_bounds(today)
        result = await list_calendar_items(start, end, intent="utility_calendar.week")
        _log_orchestrator_result(user_id, " ".join(args), result)
        await _send_result(update, context, result)
        return
    if command == "del":
        if len(args) < 2:
            result = refused(
                "Использование: /calendar del <id>.",
                intent="utility_calendar.del",
                mode="local",
            )
            _log_orchestrator_result(user_id, " ".join(args), result)
            await _send_result(update, context, result)
            return
        item_id = args[1].strip()
        if not item_id:
            result = refused("Укажите id для удаления.", intent="utility_calendar.del", mode="local")
            _log_orchestrator_result(user_id, " ".join(args), result)
            await _send_result(update, context, result)
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
        _log_orchestrator_result(user_id, " ".join(args), result)
        await _send_result(update, context, result)
        return
    if command == "debug_due":
        now = datetime.now(tz=calendar_store.VIENNA_TZ)
        due_items = await calendar_store.list_due_reminders(now, limit=5)
        if not due_items:
            result = ok("Нет просроченных напоминаний.", intent="utility_calendar.debug_due", mode="local")
            _log_orchestrator_result(user_id, " ".join(args), result)
            await _send_result(update, context, result)
            return
        lines = []
        for item in due_items:
            remind_label = item.trigger_at.astimezone(calendar_store.VIENNA_TZ).strftime("%Y-%m-%d %H:%M")
            lines.append(
                f"{item.id} | trigger_at={remind_label} | enabled={item.enabled} | {item.text}"
            )
        result = ok("\n".join(lines), intent="utility_calendar.debug_due", mode="local")
        _log_orchestrator_result(user_id, " ".join(args), result)
        await _send_result(update, context, result)
        return
    result = refused(
        "Неизвестная команда. Использование: /calendar add|list|today|week|del|debug_due.",
        intent="utility_calendar",
        mode="local",
    )
    _log_orchestrator_result(user_id, " ".join(args), result)
    await _send_result(update, context, result)


@_with_error_handling
async def reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    limit = 5
    if context.args:
        try:
            limit = max(1, int(context.args[0]))
        except ValueError:
            result = refused("Использование: /reminders [N].", intent="utility_reminders.list", mode="local")
            _log_orchestrator_result(user_id, " ".join(context.args), result)
            await _send_result(update, context, result)
            return
    now = datetime.now(tz=calendar_store.VIENNA_TZ)
    result = await list_reminders(now, limit=limit, intent="utility_reminders.list")
    _log_orchestrator_result(user_id, " ".join(context.args), result)
    await _send_result(update, context, result)


@_with_error_handling
async def reminder_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    if not context.args:
        result = refused("Использование: /reminder_off <id>.", intent="utility_reminders.off", mode="local")
        _log_orchestrator_result(user_id, "", result)
        await _send_result(update, context, result)
        return
    reminder_id = context.args[0].strip()
    if not reminder_id:
        result = refused("Укажите id напоминания.", intent="utility_reminders.off", mode="local")
        _log_orchestrator_result(user_id, " ".join(context.args), result)
        await _send_result(update, context, result)
        return
    scheduler = _get_reminder_scheduler(context)
    if scheduler:
        try:
            await scheduler.cancel_reminder(reminder_id)
        except Exception:
            LOGGER.exception("Failed to cancel reminder: reminder_id=%s", reminder_id)
            result = error(
                "Не удалось отменить напоминание.",
                intent="utility_reminders.off",
                mode="local",
            )
            _log_orchestrator_result(user_id, " ".join(context.args), result)
            await _send_result(update, context, result)
            return
    else:
        await calendar_store.disable_reminder(reminder_id)
    result = ok(
        f"Напоминание отключено: {reminder_id}",
        intent="utility_reminders.off",
        mode="local",
    )
    _log_orchestrator_result(user_id, " ".join(context.args), result)
    await _send_result(update, context, result)


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
        _log_orchestrator_result(user_id, "", result)
        await _send_result(update, context, result)
        return
    event_id = context.args[0].strip()
    if not event_id:
        result = refused("Укажите event_id.", intent="utility_reminders.on", mode="local")
        _log_orchestrator_result(user_id, " ".join(context.args), result)
        await _send_result(update, context, result)
        return
    event = await calendar_store.get_event(event_id)
    if event is None:
        result = refused(
            f"Событие не найдено: {event_id}",
            intent="utility_reminders.on",
            mode="local",
        )
        _log_orchestrator_result(user_id, " ".join(context.args), result)
        await _send_result(update, context, result)
        return
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
            result = error(
                "Не удалось включить напоминание.",
                intent="utility_reminders.on",
                mode="local",
            )
            _log_orchestrator_result(user_id, " ".join(context.args), result)
            await _send_result(update, context, result)
            return
    result = ok(
        f"Напоминание включено: {reminder.id}",
        intent="utility_reminders.on",
        mode="local",
    )
    _log_orchestrator_result(user_id, " ".join(context.args), result)
    await _send_result(update, context, result)


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
    if prompt in {
        menu.STATUS_BUTTON,
        menu.SUMMARY_BUTTON,
        menu.FACTS_TOGGLE_BUTTON,
        menu.HELP_BUTTON,
        menu.CHECK_BUTTON,
        menu.REWRITE_BUTTON,
        menu.EXPLAIN_BUTTON,
        menu.CALC_BUTTON,
        menu.CALENDAR_BUTTON,
    }:
        return
    if not prompt:
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
    _log_orchestrator_result(user_id, prompt, result)
    await _send_result(update, context, result)
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
    await safe_send_text(update, context, message)


@_with_error_handling
async def health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    await safe_send_text(update, context, _build_health_message(context))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_status(context, "error")
    LOGGER.exception("Unhandled exception", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await safe_send_text(update, context, "Ошибка на сервере. Попробуй ещё раз.")
