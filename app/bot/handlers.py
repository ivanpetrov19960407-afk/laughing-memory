from __future__ import annotations

import asyncio
import logging
import re
import os
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any
from pathlib import Path

import telegram
from telegram import InlineKeyboardMarkup, InputFile, Update
from telegram.ext import ContextTypes

from app.bot import menu, routing, wizard
from app.bot.actions import ActionStore, StoredAction, build_inline_keyboard, parse_callback_token
from app.core import calendar_store, tools_calendar
from app.core.calendar_nlp_ru import (
    EventDraft,
    event_from_text_ru,
    generate_draft_id,
    is_calendar_intent,
    parse_datetime_shift,
    update_draft_from_text,
)
from app.core.calc import CalcError, parse_and_eval
from app.core.dialog_memory import DialogMemory, DialogMessage
from app.core.document_qa import select_relevant_chunks
from app.core.last_state_resolver import ResolutionResult, resolve_short_message
from app.core.memory_layers import ActionsLogLayer, UserProfileLayer, build_memory_layers_context
from app.core.memory_store import MemoryStore
from app.core.orchestrator import Orchestrator
from app.core.file_text_extractor import FileTextExtractor, OCRNotAvailableError
from app.core.user_profile import UserProfile
from app.core.result import (
    Action,
    OrchestratorResult,
    ensure_safe_text_strict,
    ensure_valid,
    error,
    normalize_to_orchestrator_result,
    ok,
    ratelimited,
    refused,
)
from app.core.tools_calendar import create_event, delete_event, list_calendar_items, list_reminders, update_event
from app.core.recurrence_scope import RecurrenceScope, normalize_scope, parse_recurrence_scope
from app.core.tools_llm import llm_check, llm_explain, llm_rewrite
from app.infra.allowlist import AllowlistStore
from app.infra.actions_log_store import ActionsLogStore
from app.infra.last_state_store import LastStateStore
from app.infra.draft_store import DraftStore
from app.infra.document_session_store import DocumentSessionStore
from app.infra.messaging import safe_edit_text, safe_send_text
from app.infra.llm import LLMClient, PerplexityClient, ensure_plain_text
from app.infra.llm.openai_client import OpenAIClient
from app.infra.rate_limiter import RateLimiter
from app.infra.resilience import RetryPolicy, TimeoutConfig
from app.infra.request_context import (
    RequestContext,
    add_trace,
    elapsed_ms,
    get_request_context,
    log_error,
    log_event,
    log_request,
    set_input_text,
    set_status,
    start_request,
)
from app.infra.version import resolve_app_version
from app.infra.trace_store import TraceEntry, TraceStore
from app.infra.storage import TaskStorage
from app.infra.user_profile_store import UserProfileStore

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


def _get_memory_store(context: ContextTypes.DEFAULT_TYPE) -> MemoryStore | None:
    store = context.application.bot_data.get("memory_store")
    if isinstance(store, MemoryStore):
        return store
    return None


def _get_profile_store(context: ContextTypes.DEFAULT_TYPE) -> UserProfileStore | None:
    store = context.application.bot_data.get("profile_store")
    if isinstance(store, UserProfileStore):
        return store
    return None


def _get_actions_log_store(context: ContextTypes.DEFAULT_TYPE) -> ActionsLogStore | None:
    store = context.application.bot_data.get("actions_log_store")
    if isinstance(store, ActionsLogStore):
        return store
    return None


def _get_last_state_store(context: ContextTypes.DEFAULT_TYPE) -> LastStateStore | None:
    store = context.application.bot_data.get("last_state_store")
    if isinstance(store, LastStateStore):
        return store
    return None


def _get_openai_client(context: ContextTypes.DEFAULT_TYPE) -> OpenAIClient | None:
    return context.application.bot_data.get("openai_client")


def _get_llm_client(context: ContextTypes.DEFAULT_TYPE) -> LLMClient | None:
    client = context.application.bot_data.get("llm_client")
    if isinstance(client, OpenAIClient):
        return client
    if isinstance(client, PerplexityClient):
        return client
    if hasattr(client, "generate_text"):
        return client
    return None


def _get_document_store(context: ContextTypes.DEFAULT_TYPE) -> DocumentSessionStore | None:
    store = context.application.bot_data.get("document_store")
    if isinstance(store, DocumentSessionStore):
        return store
    return None


def _get_reminder_scheduler(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data.get("reminder_scheduler")


def _get_settings(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data.get("settings")


def _get_timeouts(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data.get("resilience_timeouts")


def _resolve_llm_model(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    settings = _get_settings(context)
    if settings is None:
        return None
    client = _get_llm_client(context)
    if isinstance(client, OpenAIClient):
        return settings.openai_model
    if isinstance(client, PerplexityClient):
        return settings.perplexity_model
    return settings.openai_model or settings.perplexity_model


def _get_retry_policy(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data.get("resilience_retry_policy")


def _get_circuit_breakers(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data.get("circuit_breakers")


def _get_wizard_manager(context: ContextTypes.DEFAULT_TYPE) -> wizard.WizardManager | None:
    manager = context.application.bot_data.get("wizard_manager")
    if isinstance(manager, wizard.WizardManager):
        return manager
    return None


def _get_action_store(context: ContextTypes.DEFAULT_TYPE) -> ActionStore:
    store = context.application.bot_data.get("action_store")
    if isinstance(store, ActionStore):
        return store
    store = ActionStore()
    context.application.bot_data["action_store"] = store
    return store


def _get_trace_store(context: ContextTypes.DEFAULT_TYPE) -> TraceStore | None:
    store = context.application.bot_data.get("trace_store")
    if isinstance(store, TraceStore):
        return store
    return None


def _get_draft_store(context: ContextTypes.DEFAULT_TYPE) -> DraftStore | None:
    store = context.application.bot_data.get("draft_store")
    if isinstance(store, DraftStore):
        return store
    return None


def _build_memory_context(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    request_context = get_request_context(context)
    if request_context is None:
        return None
    memory_store = _get_memory_store(context)
    profile_store = _get_profile_store(context)
    actions_store = _get_actions_log_store(context)
    profile_layer = UserProfileLayer(profile_store) if profile_store is not None else None
    actions_layer = ActionsLogLayer(actions_store) if actions_store is not None else None
    return build_memory_layers_context(
        request_context,
        memory_store=memory_store,
        profile_layer=profile_layer,
        actions_layer=actions_layer,
        max_chars=2000,
    )


def _wizards_enabled(context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = _get_settings(context)
    return bool(getattr(settings, "enable_wizards", False))


def _menu_enabled(context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = _get_settings(context)
    return bool(getattr(settings, "enable_menu", False))


def _strict_no_pseudo_sources(context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = _get_settings(context)
    return bool(getattr(settings, "strict_no_pseudo_sources", False))


def _caldav_configured(context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = _get_settings(context)
    return tools_calendar.is_caldav_configured(settings)


async def _handle_caldav_settings(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
) -> OrchestratorResult:
    if not _caldav_configured(context):
        return refused(
            "CalDAV не подключён. Укажите CALDAV_URL/USERNAME/PASSWORD в окружении.",
            intent="settings.caldav.status",
            mode="local",
            actions=[_menu_action()],
        )
    status = "CalDAV подключён."
    return ok(
        status,
        intent="settings.caldav.status",
        mode="local",
        actions=[
            Action(
                id="settings.caldav.check",
                label="Проверить подключение",
                payload={"op": "caldav_check"},
            ),
            _menu_action(),
        ],
    )


async def _handle_caldav_check(context: ContextTypes.DEFAULT_TYPE) -> OrchestratorResult:
    if not _caldav_configured(context):
        return refused(
            "CalDAV не подключён. Укажите CALDAV_URL/USERNAME/PASSWORD.",
            intent="settings.caldav.check",
            mode="local",
            actions=[_menu_action()],
        )
    ok_status, calendar_name = await tools_calendar.check_caldav_connection()
    if ok_status:
        name_suffix = f" ({calendar_name})" if calendar_name else ""
        return ok(
            f"✅ CalDAV подключён{name_suffix}.",
            intent="settings.caldav.check",
            mode="local",
            actions=[_menu_action()],
        )
    return refused(
        "❌ Не удалось подключиться к CalDAV. Проверьте URL/логин/пароль.",
        intent="settings.caldav.check",
        mode="local",
        actions=[_menu_action()],
    )


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
    request_context = get_request_context(context)
    log_event(
        LOGGER,
        request_context,
        component="router",
        event="route.selected",
        status="ok",
        user_id=user_id,
        message_type=message_type,
        command=command or "-",
        handler=handler_name,
        intent=route,
        modes=modes,
    )
    add_trace(
        request_context,
        step="route.selected",
        component="router",
        name=route,
        status="ok",
        duration_ms=None,
    )


def _record_trace_summary(context: ContextTypes.DEFAULT_TYPE, request_context: RequestContext | None) -> None:
    if request_context is None:
        return
    if not request_context.user_id or not request_context.chat_id:
        return
    store = _get_trace_store(context)
    if store is None:
        return
    total_duration_ms = elapsed_ms(request_context.start_time)
    store.add_from_context(
        chat_id=int(request_context.chat_id),
        user_id=int(request_context.user_id),
        request_context=request_context,
        total_duration_ms=total_duration_ms,
    )


def _record_user_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    request_context = get_request_context(context)
    if request_context is None:
        return
    memory_store = _get_memory_store(context)
    if memory_store is None:
        return
    chat = update.effective_chat
    if chat is None or getattr(chat, "type", "private") != "private":
        return
    message = update.effective_message
    text = ""
    if message is not None:
        text = message.text or message.caption or ""
    if not text:
        return
    memory_store.add(
        chat_id=int(request_context.chat_id or 0),
        user_id=int(request_context.user_id or 0),
        role="user",
        kind="message",
        content=text,
        correlation_id=request_context.correlation_id,
        env=request_context.env,
    )


def _with_error_handling(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]],
) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        request_context = start_request(update, context)
        log_event(
            LOGGER,
            request_context,
            component="handler",
            event="update.received",
            status="ok",
            user_id=request_context.user_id,
            chat_id=request_context.chat_id,
            message_id=request_context.message_id,
            text=request_context.input_text,
        )
        _record_user_memory(update, context)
        try:
            await _log_route(update, context, handler.__name__)
            await handler(update, context)
        except Exception as exc:
            set_status(context, "error")
            log_error(
                LOGGER,
                request_context,
                component="handler",
                where="handler.wrapper",
                exc=exc,
                extra={"handler": handler.__name__},
            )
            await _handle_exception(update, context, exc)
        finally:
            log_request(LOGGER, request_context)
            _record_trace_summary(context, request_context)

    return wrapper


@_with_error_handling
async def caldav_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context, bucket="ui"):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    result = await _handle_caldav_settings(context, user_id=user_id)
    await send_result(update, context, result)


async def _handle_exception(update: Update, context: ContextTypes.DEFAULT_TYPE, error: Exception) -> None:
    process_error = getattr(context.application, "process_error", None)
    if not callable(process_error):
        LOGGER.exception("Unhandled exception", exc_info=error)
        return
    try:
        await process_error(update, error)
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
            intent="rate.limit",
            mode="local",
            debug={"scope": result.scope, "retry_after": result.retry_after},
        )
        await send_result(update, context, result_message)
        return False
    return True


def _is_group_chat(update: Update) -> bool:
    chat = update.effective_chat
    chat_type = getattr(chat, "type", None)
    return chat_type in {"group", "supergroup"}


def _handle_trace_request(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    chat_id: int,
    correlation_id: str | None = None,
    use_last: bool = False,
) -> OrchestratorResult:
    store = _get_trace_store(context)
    if store is None:
        return _build_simple_result(
            "Трассы недоступны.",
            intent="command.trace",
            status="refused",
            mode="local",
        )
    if correlation_id:
        matches = store.find_entries(chat_id=chat_id, user_id=user_id, correlation_id=correlation_id)
        if not matches:
            return _build_simple_result(
                "Трасса не найдена.",
                intent="command.trace",
                status="refused",
                mode="local",
            )
        if len(matches) > 1:
            return _build_simple_result(
                "Найдено несколько совпадений, укажи больше символов.",
                intent="command.trace",
                status="refused",
                mode="local",
            )
        entry = matches[0]
        return _build_simple_result(
            _format_trace_detail(entry),
            intent="command.trace.detail",
            status="ok",
            mode="local",
        )
    if use_last:
        entry = store.get_last_entry(chat_id=chat_id, user_id=user_id)
        if entry is None:
            return _build_simple_result(
                "Трассы не найдены.",
                intent="command.trace",
                status="refused",
                mode="local",
            )
        return _build_simple_result(
            _format_trace_detail(entry),
            intent="command.trace.detail",
            status="ok",
            mode="local",
        )
    entries = store.list_entries(chat_id=chat_id, user_id=user_id, limit=5)
    return _build_simple_result(
        _format_trace_list(entries),
        intent="command.trace",
        status="ok",
        mode="local",
    )


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
        intent="access.denied",
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


def _format_profile(profile: UserProfile) -> str:
    reminders = profile.default_reminders
    reminder_offset = f"{reminders.offset_minutes} минут" if reminders.offset_minutes is not None else "не задано"
    facts_label = "вкл" if profile.facts_mode_default else "выкл"
    reminders_label = "вкл" if reminders.enabled else "выкл"
    lines = [
        "Профиль пользователя:",
        f"- язык: {profile.language}",
        f"- таймзона: {profile.timezone}",
        f"- подробность: {profile.verbosity}",
        f"- режим фактов по умолчанию: {facts_label}",
        f"- напоминания по умолчанию: {reminders_label}",
        f"- смещение напоминаний: {reminder_offset}",
    ]
    if profile.style:
        lines.append(f"- стиль: {profile.style}")
    if profile.notes:
        lines.append("Заметки:")
        for note in profile.notes[:5]:
            lines.append(f"- {note.text} (id: {note.id})")
    return "\n".join(lines)


def _format_actions_history(entries: list[object]) -> str:
    if not entries:
        return "Ок. История действий пуста."
    lines = ["Последние действия:"]
    for entry in entries:
        ts_value = getattr(entry, "ts", None)
        ts_label = ts_value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M") if ts_value else "-"
        action_type = getattr(entry, "action_type", "-")
        payload = getattr(entry, "payload", {}) if isinstance(entry, object) else {}
        summary = payload.get("summary") if isinstance(payload, dict) else None
        if isinstance(summary, str) and summary.strip():
            text = summary.strip()
        else:
            text = action_type
        lines.append(f"- {ts_label} | {action_type} | {text}")
    return "\n".join(lines)


async def _reply_with_history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str,
) -> None:
    user_id = update.effective_user.id if update.effective_user else 0
    history = _append_history(context, user_id, "user", prompt)
    response = _format_history(history)
    _append_history(context, user_id, "assistant", response)
    result = _build_simple_result(response, intent="history.view", status="ok", mode="local")
    await send_result(update, context, result)


def _build_user_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict[str, object]:
    user_id = update.effective_user.id if update.effective_user else 0
    request_context = get_request_context(context)
    payload: dict[str, object] = {"user_id": user_id}
    if request_context:
        payload["request_id"] = request_context.correlation_id
        payload["request_context"] = request_context
    memory_context = _build_memory_context(context)
    if memory_context:
        payload["memory_context"] = memory_context
    return payload


def _build_menu_actions(context: ContextTypes.DEFAULT_TYPE, *, user_id: int) -> list[Action]:
    orchestrator = _get_orchestrator(context)
    facts_enabled = bool(user_id) and orchestrator.is_facts_only(user_id)
    return menu.build_menu_actions(facts_enabled=facts_enabled, enable_menu=_menu_enabled(context))


def _build_simple_result(
    text: str,
    *,
    intent: str,
    status: str = "ok",
    mode: str = "local",
    debug: dict[str, Any] | None = None,
) -> OrchestratorResult:
    if status == "ok":
        return ensure_valid(ok(text, intent=intent, mode=mode, debug=debug))
    if status == "refused":
        return ensure_valid(refused(text, intent=intent, mode=mode, debug=debug))
    if status == "ratelimited":
        return ensure_valid(ratelimited(text, intent=intent, mode=mode, debug=debug))
    return ensure_valid(error(text, intent=intent, mode=mode, debug=debug))


def _extract_result_refs(result: OrchestratorResult) -> dict[str, str]:
    refs: dict[str, str] = {}
    debug = result.debug if isinstance(result.debug, dict) else {}
    debug_refs = debug.get("refs")
    if isinstance(debug_refs, dict):
        for key, value in debug_refs.items():
            if isinstance(key, str) and isinstance(value, str) and value.strip():
                refs[key] = value
    for key in ("event_id", "reminder_id", "calendar_id", "query"):
        value = debug.get(key)
        if isinstance(value, str) and value.strip():
            refs[key] = value
    return refs


def _update_last_state(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    result: OrchestratorResult,
    user_id: int,
    chat_id: int,
    request_context: RequestContext | None,
) -> None:
    if result.status != "ok" or not user_id or not chat_id:
        return
    store = _get_last_state_store(context)
    if store is None:
        return
    refs = _extract_result_refs(result)
    correlation_id = None
    if request_context and request_context.correlation_id:
        correlation_id = request_context.correlation_id
    elif result.request_id:
        correlation_id = result.request_id
    store.update_state(
        chat_id=chat_id,
        user_id=user_id,
        intent=result.intent,
        correlation_id=correlation_id,
        event_id=refs.get("event_id"),
        reminder_id=refs.get("reminder_id"),
        calendar_id=refs.get("calendar_id"),
        query=refs.get("query"),
    )


def _log_action_from_result(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    result: OrchestratorResult,
    user_id: int,
    request_context: RequestContext | None,
) -> None:
    if result.status != "ok" or not user_id:
        return
    actions_store = _get_actions_log_store(context)
    if actions_store is None:
        return
    intent = result.intent or ""
    mapping = {
        "utility_calendar.add": "calendar.event.create",
        "utility_calendar.delete": "calendar.event.delete",
        "utility_calendar.update": "calendar.event.update",
        "utility_calendar.move": "calendar.event.update",
        "utility_reminders.create": "reminder.create",
        "utility_reminders.add": "reminder.create",
        "utility_reminders.delete": "reminder.delete",
        "utility_reminders.disable": "reminder.disable",
        "utility_reminders.off": "reminder.disable",
        "utility_reminders.on": "reminder.enable",
        "utility_reminders.reschedule": "reminder.reschedule",
        "utility_reminders.snooze": "reminder.snooze",
        "command.facts_on": "mode.facts_on",
        "command.facts_off": "mode.facts_off",
        "command.context_on": "mode.context_on",
        "command.context_off": "mode.context_off",
        "command.context_clear": "mode.context_clear",
        "wizard.profile.done": "profile.update",
    }
    action_type = mapping.get(intent)
    if not action_type:
        return
    summary = (result.text or "").replace("\n", " ").strip()
    if len(summary) > 160:
        summary = summary[:160].rstrip() + "…"
    payload = {
        "intent": intent,
        "summary": summary or action_type,
        "refs": _extract_result_refs(result),
    }
    correlation_id = request_context.correlation_id if request_context else result.request_id
    actions_store.append(
        user_id=user_id,
        action_type=action_type,
        payload=payload,
        ts=request_context.ts if request_context else None,
        correlation_id=correlation_id,
    )


def _build_last_state_actions(action: str) -> list[Action]:
    return [
        Action(
            id="memory.last_event",
            label="Последнее событие",
            payload={"op": "last_state_action", "action": action, "ref": "event"},
        ),
        Action(
            id="memory.last_reminder",
            label="Последнее напоминание",
            payload={"op": "last_state_action", "action": action, "ref": "reminder"},
        ),
        Action(
            id="memory.last_search",
            label="Последний поиск",
            payload={"op": "last_state_action", "action": action, "ref": "search"},
        ),
        _menu_action(),
    ]


def _build_resolution_fallback(action: str, *, reason: str) -> OrchestratorResult:
    return ok(
        "Уточни, пожалуйста, что именно:",
        intent="memory.resolve",
        mode="local",
        actions=_build_last_state_actions(action),
        debug={"reason": reason},
    )


def _log_memory_resolution(
    request_context: RequestContext | None,
    *,
    used: bool,
    reason: str,
    matched_ref: str | None,
) -> None:
    log_event(
        LOGGER,
        request_context,
        component="memory",
        event="memory.resolution",
        status="ok",
        used=used,
        reason=reason,
        matched_ref=matched_ref or "-",
    )


def _menu_action() -> Action:
    return Action(id="menu.open", label="🏠 Меню", payload={"op": "menu_open"})


def _has_menu_action(actions: list[Action]) -> bool:
    for action in actions:
        payload = action.payload or {}
        op = payload.get("op")
        if op == "menu_open":
            return True
        if op == "menu_section" and payload.get("section") == "home":
            return True
    return False


def _document_actions(doc_id: str) -> list[Action]:
    return [
        Action(
            id="document.summary",
            label="📝 Сделать резюме",
            payload={"op": "document.summary", "doc_id": doc_id},
        ),
        Action(
            id="document.qa",
            label="❓ Вопрос по документу",
            payload={"op": "document.qa", "doc_id": doc_id},
        ),
        Action(
            id="document.close",
            label="🗑 Закрыть документ",
            payload={"op": "document.close", "doc_id": doc_id},
        ),
    ]


def _document_qa_actions(doc_id: str) -> list[Action]:
    return [
        Action(
            id="document.qa_exit",
            label="🚪 Выйти из Q&A",
            payload={"op": "document.qa_exit", "doc_id": doc_id},
        ),
        Action(
            id="document.close",
            label="🗑 Закрыть документ",
            payload={"op": "document.close", "doc_id": doc_id},
        ),
    ]


def _mime_extension(mime_type: str) -> str:
    mapping = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    return mapping.get(mime_type, "")


def _detect_document_type(document: telegram.Document) -> tuple[str, str] | None:
    mime_type = document.mime_type or ""
    filename = document.file_name or ""
    suffix = Path(filename).suffix.lower()
    if mime_type == "application/pdf" or suffix == ".pdf":
        return "pdf", suffix or ".pdf"
    if (
        mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or suffix == ".docx"
    ):
        return "docx", suffix or ".docx"
    if mime_type.startswith("image/"):
        ext = suffix or _mime_extension(mime_type) or ".png"
        return "image", ext
    return None


def _load_document_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _trim_document_text(text: str, *, max_chars: int = 8000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit("\n", 1)[0].strip() or text[:max_chars]


async def _handle_document_summary(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    chat_id: int,
    doc_id: str,
) -> OrchestratorResult:
    document_store = _get_document_store(context)
    if document_store is None:
        return error("Хранилище документов недоступно.", intent="document.summary", mode="local")
    session = document_store.get_session(doc_id) or document_store.get_active(user_id=user_id, chat_id=chat_id)
    if session is None:
        return refused("Сначала пришлите файл.", intent="document.summary", mode="local")
    text = _load_document_text(session.text_path)
    if not text.strip():
        return error("Текст документа не найден.", intent="document.summary", mode="local")
    llm_client = _get_llm_client(context)
    model = _resolve_llm_model(context)
    if llm_client is None or model is None:
        return error("LLM не настроен.", intent="document.summary", mode="local")
    orchestrator = _get_orchestrator(context)
    facts_only = orchestrator.is_facts_only(user_id)
    system_prompt = (
        "Ты помощник. Сделай краткое тезисное резюме по документу. "
        "Используй только текст документа."
    )
    if facts_only:
        system_prompt += " Не добавляй домыслы. Если данных нет, так и скажи."
    trimmed_text = _trim_document_text(text)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Текст документа:\n{trimmed_text}\n\nСделай резюме."},
    ]
    try:
        response = await llm_client.generate_text(model=model, messages=messages)
        response = ensure_plain_text(response)
    except Exception:
        return error("Не удалось получить резюме.", intent="document.summary", mode="local")
    if not response.strip():
        return error("Не удалось получить резюме.", intent="document.summary", mode="local")
    return ok(
        response.strip(),
        intent="document.summary",
        mode="local",
        actions=_document_actions(session.doc_id),
    )


async def _handle_document_question(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    chat_id: int,
    question: str,
) -> OrchestratorResult:
    document_store = _get_document_store(context)
    if document_store is None:
        return error("Хранилище документов недоступно.", intent="document.qa", mode="local")
    session = document_store.get_active(user_id=user_id, chat_id=chat_id)
    if session is None or session.state != "qa_mode":
        return refused("Сначала пришлите файл.", intent="document.qa", mode="local")
    text = _load_document_text(session.text_path)
    if not text.strip():
        return error("Текст документа не найден.", intent="document.qa", mode="local")
    llm_client = _get_llm_client(context)
    model = _resolve_llm_model(context)
    if llm_client is None or model is None:
        return error("LLM не настроен.", intent="document.qa", mode="local")
    orchestrator = _get_orchestrator(context)
    facts_only = orchestrator.is_facts_only(user_id)
    chunks = select_relevant_chunks(text, question, top_k=4)
    if not chunks:
        return refused("В документе нет ответа.", intent="document.qa", mode="local")
    system_prompt = (
        "Отвечай только на основе предоставленных фрагментов документа. "
        "Если ответа нет во фрагментах, скажи: \"В документе нет ответа\"."
    )
    if facts_only:
        system_prompt += " Никаких домыслов, только факты из текста."
    context_text = "\n\n".join(f"Фрагмент {idx + 1}:\n{chunk}" for idx, chunk in enumerate(chunks))
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Вопрос: {question}\n\n{context_text}"},
    ]
    try:
        response = await llm_client.generate_text(model=model, messages=messages)
        response = ensure_plain_text(response)
    except Exception:
        return error("Не удалось получить ответ.", intent="document.qa", mode="local")
    if not response.strip():
        return error("Не удалось получить ответ.", intent="document.qa", mode="local")
    return ok(
        response.strip(),
        intent="document.qa",
        mode="local",
        actions=_document_qa_actions(session.doc_id),
    )


def _calendar_list_controls_actions() -> list[Action]:
    return [
        Action(id="utility_calendar.add", label="➕ Добавить", payload={"op": "calendar.add"}),
        Action(id="utility_calendar.list", label="🔄 Обновить", payload={"op": "calendar.list"}),
        _menu_action(),
    ]


def _build_recurrence_scope_actions(
    op: str,
    *,
    event_id: str,
    instance_dt: datetime | None,
    extra_payload: dict[str, object] | None = None,
) -> list[Action]:
    payload_base: dict[str, object] = {"op": op, "event_id": event_id}
    if instance_dt is not None:
        payload_base["instance_dt"] = instance_dt.isoformat()
    if extra_payload:
        payload_base.update(extra_payload)
    return [
        Action(
            id=f"{op}.scope.this",
            label="Только это",
            payload={**payload_base, "scope": RecurrenceScope.THIS.value},
        ),
        Action(
            id=f"{op}.scope.all",
            label="Всю серию",
            payload={**payload_base, "scope": RecurrenceScope.ALL.value},
        ),
        Action(
            id=f"{op}.scope.future",
            label="Это и будущие",
            payload={**payload_base, "scope": RecurrenceScope.FUTURE.value},
        ),
    ]


def _reminder_list_controls_actions() -> list[Action]:
    return [
        Action(id="utility_reminders.create", label="➕ Создать", payload={"op": "reminder.create"}),
        Action(id="utility_reminders.list", label="🔄 Обновить", payload={"op": "reminder.list", "limit": 10}),
        _menu_action(),
    ]


def _reminder_delete_confirm_actions(reminder_id: str) -> list[Action]:
    return [
        Action(
            id="utility_reminders.delete_confirmed",
            label="✅ Удалить",
            payload={"op": "reminder.delete_confirmed", "reminder_id": reminder_id},
        ),
        Action(
            id="utility_reminders.delete_cancel",
            label="↩ Отмена",
            payload={"op": "reminder.list", "limit": 10},
        ),
    ]


def _map_wizard_target(target: str | None) -> str | None:
    if not target:
        return None
    normalized = target.strip().lower()
    if normalized in {"reminders.create", "reminder.create"}:
        return wizard.WIZARD_REMINDER_CREATE
    if normalized in {"calendar.add", "calendar.create"}:
        return wizard.WIZARD_CALENDAR_ADD
    if normalized in {"reminder.reschedule"}:
        return wizard.WIZARD_REMINDER_RESCHEDULE
    return None


async def _build_reminders_list_result(
    now: datetime,
    *,
    user_id: int,
    chat_id: int,
    limit: int,
    intent: str,
) -> OrchestratorResult:
    items = await calendar_store.list_reminders(now, limit=None, include_disabled=False)
    filtered = [item for item in items if item.user_id == user_id and item.chat_id == chat_id]
    filtered.sort(key=lambda item: item.trigger_at)
    limited = filtered[: max(1, limit)]
    actions = _reminder_list_controls_actions()
    if not limited:
        return ok("Нет запланированных напоминаний.", intent=intent, mode="local", actions=actions)
    lines: list[str] = []
    for item in limited:
        when_label = item.trigger_at.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{item.id} | {when_label} | {item.text}")
        actions.append(
            Action(
                id="utility_reminders.delete",
                label=f"🗑 Удалить: {_short_label(item.text)}",
                payload={"op": "reminder.delete_confirm", "reminder_id": item.id},
            )
        )
        actions.append(
            Action(
                id="utility_reminders.reschedule",
                label=f"✏ Перенести: {_short_label(item.text)}",
                payload={"op": "reminder_reschedule", "reminder_id": item.id, "base_trigger_at": item.trigger_at.isoformat()},
            )
        )
    return ok("\n".join(lines), intent=intent, mode="local", actions=actions)


async def _build_calendar_list_result(
    start: datetime,
    end: datetime,
    *,
    user_id: int,
    chat_id: int,
    intent: str,
) -> OrchestratorResult:
    items = await calendar_store.list_items(start=start, end=end)
    filtered = [item for item in items if item.user_id == user_id and item.chat_id == chat_id]
    filtered.sort(key=lambda item: item.dt)
    actions = _calendar_list_controls_actions()
    if not filtered:
        return ok("Нет событий на ближайшие 7 дней.", intent=intent, mode="local", actions=actions)
    lines: list[str] = []
    for item in filtered:
        dt_label = item.dt.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{item.id} | {dt_label} | {item.title}")
        actions.append(
            Action(
                id="utility_calendar.delete",
                label=f"🗑 Удалить: {_short_label(item.title)}",
                payload={"op": "calendar.delete", "event_id": item.id},
            )
        )
    return ok("\n".join(lines), intent=intent, mode="local", actions=actions)


def _short_label(value: str, limit: int = 24) -> str:
    cleaned = value.strip().replace("\n", " ")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _render_event_draft(draft: EventDraft) -> str:
    lines = ["🗓 Черновик события"]
    lines.append(f"Название: {draft.title}")
    when_label = "—"
    if draft.start_at is not None:
        start_local = draft.start_at.astimezone(calendar_store.BOT_TZ)
        when_label = start_local.strftime("%d.%m.%Y %H:%M")
    elif draft.date_hint is not None:
        when_label = draft.date_hint.strftime("%d.%m.%Y")
    lines.append(f"Когда: {when_label}")
    if draft.duration_minutes:
        lines.append(f"Длительность: {draft.duration_minutes} мин")
    elif draft.end_at is not None:
        end_local = draft.end_at.astimezone(calendar_store.BOT_TZ)
        lines.append(f"До: {end_local.strftime('%H:%M')}")
    if draft.recurrence is not None:
        lines.append(f"Повтор: {draft.recurrence.human}")
    if draft.location:
        lines.append(f"Место: {draft.location}")
    if draft.missing_fields:
        missing_map = {"title": "название", "date": "дату", "time": "время"}
        missing_labels = [missing_map.get(field, field) for field in draft.missing_fields]
        lines.append(f"Нужно уточнить: {', '.join(missing_labels)}.")
    return "\n".join(lines)


def _draft_actions(draft_id: str) -> list[Action]:
    return [
        Action(
            id="calendar.create_confirm",
            label="✅ Подтвердить",
            payload={"op": "calendar.create_confirm", "draft_id": draft_id},
        ),
        Action(
            id="calendar.create_edit",
            label="✏️ Изменить",
            payload={"op": "calendar.create_edit", "draft_id": draft_id},
        ),
        Action(
            id="calendar.create_cancel",
            label="❌ Отмена",
            payload={"op": "calendar.create_cancel", "draft_id": draft_id},
        ),
    ]


def _draft_missing_prompt(draft: EventDraft) -> str:
    if "title" in draft.missing_fields:
        return "Как назвать событие?"
    if "date" in draft.missing_fields:
        return "На какую дату?"
    if "time" in draft.missing_fields:
        return "Во сколько?"
    return "Что уточнить?"


def _build_user_context_with_dialog(
    update: Update,
    *,
    dialog_context: str | None,
    dialog_message_count: int,
    memory_context: str | None,
    request_id: str | None,
    request_context: RequestContext | None,
) -> dict[str, object]:
    user_id = update.effective_user.id if update.effective_user else 0
    payload: dict[str, object] = {"user_id": user_id}
    if dialog_context:
        payload["dialog_context"] = dialog_context
        payload["dialog_message_count"] = dialog_message_count
    if memory_context:
        payload["memory_context"] = memory_context
    if request_id:
        payload["request_id"] = request_id
    if request_context:
        payload["request_context"] = request_context
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
    return {
        "user_id": user_id,
        "orchestrator": _get_orchestrator(context),
        "request_context": get_request_context(context),
    }


def _log_orchestrator_result(
    user_id: int,
    result: OrchestratorResult,
    *,
    request_context: RequestContext | None = None,
) -> None:
    log_event(
        LOGGER,
        request_context,
        component="orchestrator",
        event="result.normalized",
        status=result.status,
        user_id=user_id,
        intent=result.intent,
        mode=result.mode,
        sources=len(result.sources),
        actions=len(result.actions),
        attachments=len(result.attachments),
        response=result.text,
        debug=result.debug,
    )


async def _send_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None) -> None:
    if update.callback_query and not isinstance(reply_markup, telegram.ReplyKeyboardRemove):
        await safe_edit_text(update, context, text, reply_markup=reply_markup)
        return
    await safe_send_text(update, context, text, reply_markup=reply_markup)


def _render_text_with_sources(text: str, sources: list[Any]) -> str:
    base = (text or "").rstrip()
    if not sources:
        return base
    if "\nИсточники:\n" in base or base.endswith("\nИсточники:"):
        return base
    from app.core.facts import format_sources_block
    from app.core.result import Source

    normalized: list[Source] = []
    for source in sources:
        if isinstance(source, Source):
            normalized.append(source)
            continue
        if isinstance(source, dict):
            title = str(source.get("title") or "")
            url = str(source.get("url") or "")
            snippet = str(source.get("snippet") or "")
            normalized.append(Source(title=title, url=url, snippet=snippet))
            continue
        url = str(getattr(source, "url", "") or "")
        title = str(getattr(source, "title", "") or url)
        snippet = str(getattr(source, "snippet", "") or "")
        normalized.append(Source(title=title, url=url, snippet=snippet))
    return f"{base}\n\n{format_sources_block(normalized)}"


def _apply_strict_pseudo_source_guard(text: str | None) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"\[\s*\d+\s*\]", "", cleaned)
    return cleaned


def _format_trace_timestamp(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _format_trace_list(entries: list[TraceEntry]) -> str:
    if not entries:
        return "Трассы не найдены."
    lines = ["Последние запросы:"]
    for entry in entries:
        intent = entry.intent or "-"
        short_id = entry.correlation_id[:8]
        lines.append(f"- {_format_trace_timestamp(entry.ts)} | {intent} | {entry.status} | {short_id}")
    return "\n".join(lines)


def _format_trace_detail(entry: TraceEntry) -> str:
    lines = [
        f"Trace {entry.correlation_id}",
        f"intent: {entry.intent or '-'}",
        f"mode: {entry.mode or '-'}",
        f"status: {entry.status}",
    ]
    if entry.total_duration_ms is not None:
        lines.append(f"total_duration_ms: {entry.total_duration_ms:.2f}")
    lines.append("")
    lines.append("Steps:")
    lines.append("step | component | name | status | duration_ms")
    for step in entry.trace_steps:
        duration = step.get("duration_ms")
        duration_text = f"{duration:.2f}" if isinstance(duration, (int, float)) else "-"
        name = step.get("name") or "-"
        lines.append(
            f"{step.get('step')} | {step.get('component')} | {name} | {step.get('status')} | {duration_text}"
        )
    if entry.tool_calls:
        lines.append("")
        lines.append("Tool calls:")
        for call in entry.tool_calls:
            duration = call.get("duration_ms")
            duration_text = f"{duration:.2f}" if isinstance(duration, (int, float)) else "-"
            lines.append(f"- {call.get('name') or '-'}: {duration_text} ms")
    if entry.llm_calls:
        lines.append("")
        lines.append("LLM calls:")
        for call in entry.llm_calls:
            duration = call.get("duration_ms")
            duration_text = f"{duration:.2f}" if isinstance(duration, (int, float)) else "-"
            lines.append(f"- {call.get('name') or '-'}: {duration_text} ms")
    if entry.error:
        lines.append("")
        lines.append(f"error: {entry.error.get('exc_type')} at {entry.error.get('where')}")
    return "\n".join(lines)



async def _send_reply_keyboard_remove(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str = "Открываю меню…",
) -> None:
    await safe_send_text(update, context, text, reply_markup=telegram.ReplyKeyboardRemove())


async def _safe_answer_callback(query: telegram.CallbackQuery, text: str | None = None) -> None:
    try:
        try:
            await query.answer(text)
        except TypeError:
            await query.answer()
    except telegram.error.BadRequest as exc:
        message = str(exc)
        if "Query is too old" in message or "response timeout expired" in message or "query id is invalid" in message:
            LOGGER.debug("Callback query expired: %s", message)
            return
        LOGGER.warning("Failed to answer callback query: %s", message)
    except Exception:
        LOGGER.exception("Failed to answer callback query")


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
    result: OrchestratorResult | dict[str, Any] | None,
    *,
    reply_markup=None,
) -> None:
    public_result = normalize_to_orchestrator_result(result)
    user_id = update.effective_user.id if update.effective_user else 0
    facts_enabled = False
    orchestrator = context.application.bot_data.get("orchestrator")
    if isinstance(orchestrator, Orchestrator) and user_id:
        facts_enabled = orchestrator.is_facts_only(user_id)
    public_result = ensure_valid(ensure_safe_text_strict(public_result, facts_enabled, allow_sources_in_text=False))
    if not public_result.text.strip():
        # Replace empty text with fallback message while preserving all other fields
        public_result = replace(public_result, text="Нет ответа.")
    chat_id = update.effective_chat.id if update.effective_chat else 0
    request_context = get_request_context(context)
    request_id = request_context.correlation_id if request_context else None
    if request_context and not public_result.request_id:
        public_result = replace(public_result, request_id=request_context.correlation_id)
    if request_context:
        debug_payload = dict(public_result.debug)
        debug_payload.setdefault("trace", request_context.trace)
        public_result = replace(public_result, debug=debug_payload)
        request_context.meta.setdefault("intent", public_result.intent)
        request_context.meta.setdefault("mode", public_result.mode)
        if request_context.env == "dev":
            if not any(action.id == "debug.trace" for action in public_result.actions):
                public_result = replace(
                    public_result,
                    actions=[
                        *public_result.actions,
                        Action(id="debug.trace", label="Trace", payload={"op": "trace_last"}),
                    ],
                )
    if public_result.status in {"refused", "error"} and not _has_menu_action(public_result.actions):
        public_result = replace(public_result, actions=[*public_result.actions, _menu_action()])
    if request_id:
        sent_key = f"send_result:{request_id}"
        if context.chat_data.get(sent_key):
            LOGGER.warning("send_result skipped duplicate: request_id=%s intent=%s", request_id, public_result.intent)
            return
        context.chat_data[sent_key] = True
    memory_store = _get_memory_store(context)
    if memory_store and user_id and chat_id:
        chat = update.effective_chat
        if chat is not None and getattr(chat, "type", "private") == "private":
            memory_store.add(
                chat_id=chat_id,
                user_id=user_id,
                role="assistant",
                kind="result",
                content=public_result.text,
                intent=public_result.intent,
                status=public_result.status,
                correlation_id=request_id,
                env=request_context.env if request_context else "prod",
            )
    _log_orchestrator_result(user_id, public_result, request_context=request_context)
    _update_last_state(
        context,
        result=public_result,
        user_id=user_id,
        chat_id=chat_id,
        request_context=request_context,
    )
    _log_action_from_result(
        context,
        result=public_result,
        user_id=user_id,
        request_context=request_context,
    )
    guarded_text = public_result.text
    if _strict_no_pseudo_sources(context):
        guarded_text = _apply_strict_pseudo_source_guard(public_result.text)
    final_text = _render_text_with_sources(guarded_text, public_result.sources)
    output_preview = final_text.replace("\n", " ").strip()
    if len(output_preview) > 80:
        output_preview = f"{output_preview[:80].rstrip()}…"
    inline_keyboard = build_inline_keyboard(
        public_result.actions,
        store=_get_action_store(context),
        user_id=user_id,
        chat_id=chat_id,
    )
    effective_reply_markup = inline_keyboard if inline_keyboard is not None else reply_markup
    LOGGER.info(
        f"UI send: status={public_result.status} "
        f"actions={len(public_result.actions)} "
        f"reply_markup={effective_reply_markup is not None}",
    )
    send_start = time.monotonic()
    await _send_text(update, context, final_text, reply_markup=effective_reply_markup)
    await _send_attachments(update, context, public_result.attachments)
    if request_id:
        total_duration_ms = None
        if request_context:
            total_duration_ms = elapsed_ms(request_context.start_time)
        log_event(
            LOGGER,
            request_context,
            component="handler",
            event="response.sent",
            status=public_result.status,
            duration_ms=total_duration_ms,
            intent=public_result.intent,
            output_preview=output_preview,
        )
        add_trace(
            request_context,
            step="response.sent",
            component="handler",
            name="send_result",
            status=public_result.status,
            duration_ms=elapsed_ms(send_start),
        )


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
        "Привет! Я бот-оркестратор задач и напоминаний.\n"
        f"Конфигурация: {title} (v{version}).\n"
        "Основной вход — /menu.\n"
        "Можно писать обычные сообщения — подскажу дальше."
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
async def trace_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context, bucket="ui"):
        return
    if _is_group_chat(update):
        result = _build_simple_result(
            "Команда /trace недоступна в группах.",
            intent="command.trace",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    if not user_id or not chat_id:
        result = _build_simple_result(
            "Не удалось определить пользователя.",
            intent="command.trace",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    args_text = " ".join(context.args) if context.args else ""
    result = _handle_trace_request(
        context,
        user_id=user_id,
        chat_id=chat_id,
        correlation_id=args_text or None,
    )
    await send_result(update, context, result)


@_with_error_handling
async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    result = refused(
        "Неизвестная команда. Открой меню.",
        intent="command.unknown",
        mode="local",
        actions=[_menu_action()],
    )
    await send_result(update, context, result)


def _build_help_text(access_note: str) -> str:
    return (
        "Команды:\n"
        "/start — приветствие\n"
        "/help — помощь\n"
        "/menu — открыть меню\n"
        "/ping — проверка связи\n"
        "/reminders — ближайшие напоминания\n"
        "/tasks — список задач\n"
        "/task <name> <payload> — выполнить задачу\n\n"
        "Как пользоваться:\n"
        "1) Открой /menu.\n"
        "2) Нажимай кнопки — они ведут к сценариям.\n"
        "3) Можно писать текстом, если знаешь, что нужно.\n"
        "4) Для задач используй /tasks и /task.\n"
        + access_note
    )


async def _build_health_message(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int | None = None,
) -> str:
    settings = context.application.bot_data["settings"]
    request_context = get_request_context(context)
    env_label = request_context.env if request_context else "prod"
    start_time = context.application.bot_data.get("start_time", time.monotonic())
    uptime = _format_uptime(start_time)
    orchestrator = _get_orchestrator(context)
    version = resolve_app_version(orchestrator.config.get("system_metadata", {}))
    timezone_label = calendar_store.BOT_TZ.key
    caldav_status = "ok" if tools_calendar.is_caldav_configured(settings) else "error"
    llm_status = "ok" if settings.openai_api_key or settings.perplexity_api_key else "error"
    store = calendar_store.load_store()
    reminders_count = len(store.get("reminders") or [])
    memory_store = _get_memory_store(context)
    memory_count = memory_store.count_entries() if memory_store else 0
    trace_store = _get_trace_store(context)
    trace_count = trace_store.count_entries() if trace_store else 0
    breaker_registry = _get_circuit_breakers(context)
    breaker_states = breaker_registry.snapshot() if breaker_registry else {}
    breaker_label = ", ".join(f"{name}={state}" for name, state in breaker_states.items()) or "none"
    return (
        "Health:\n"
        f"App: v{version}, uptime {uptime}, env {env_label}, tz {timezone_label}\n"
        f"Integrations: CalDAV {caldav_status}, LLM {llm_status}\n"
        f"Stores: reminders {reminders_count}, memory {memory_count}, trace {trace_count}\n"
        f"Circuit breakers: {breaker_label}"
    )


def _build_config_message(context: ContextTypes.DEFAULT_TYPE) -> str:
    settings = context.application.bot_data["settings"]
    timeouts = _get_timeouts(context) or TimeoutConfig()
    retry_policy = _get_retry_policy(context) or RetryPolicy()
    circuit_breakers = _get_circuit_breakers(context)
    log_level = logging.getLevelName(logging.getLogger().getEffectiveLevel())
    integrations = {
        "caldav": bool(settings.caldav_url and settings.caldav_username and settings.caldav_password),
        "openai": bool(settings.openai_api_key),
        "perplexity": bool(settings.perplexity_api_key),
        "web_search": bool(settings.feature_web_search),
    }
    breaker_config = circuit_breakers.config if circuit_breakers else None
    breaker_line = "n/a"
    if breaker_config:
        breaker_line = (
            f"failure_threshold={breaker_config.failure_threshold}, "
            f"window_seconds={breaker_config.window_seconds}, "
            f"cooldown_seconds={breaker_config.cooldown_seconds}"
        )
    storage_lines = [
        f"db_path={settings.db_path}",
        f"allowlist_path={settings.allowlist_path}",
        f"dialog_memory_path={settings.dialog_memory_path}",
        f"wizard_store_path={settings.wizard_store_path}",
    ]
    lines = [
        "Config:",
        f"env={os.getenv('APP_ENV', 'prod')}",
        f"calendar_backend={settings.calendar_backend}",
        f"reminders_enabled={settings.reminders_enabled}",
        f"enable_wizards={settings.enable_wizards}",
        f"enable_menu={settings.enable_menu}",
        f"strict_no_pseudo_sources={settings.strict_no_pseudo_sources}",
        f"log_level={log_level}",
        "integrations=" + ", ".join(f"{key}={'on' if value else 'off'}" for key, value in integrations.items()),
        f"timeouts=tool:{timeouts.tool_call_seconds}s web:{timeouts.web_tool_call_seconds}s llm:{timeouts.llm_seconds}s ext:{timeouts.external_api_seconds}s",
        f"retry=max_attempts={retry_policy.max_attempts} base_delay_ms={retry_policy.base_delay_ms} max_delay_ms={retry_policy.max_delay_ms} jitter_ms={retry_policy.jitter_ms}",
        f"breaker={breaker_line}",
        "storage=" + ", ".join(storage_lines),
    ]
    return "\n".join(lines)


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
        tool_result = orchestrator.execute_task(
            user_id,
            task_name,
            payload,
            request_context=get_request_context(context),
        )
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
    request_id = request_context.correlation_id if request_context else None
    memory_context = _build_memory_context(context)
    try:
        result = await orchestrator.handle(
            f"/ask {prompt}",
            _build_user_context_with_dialog(
                update,
                dialog_context=dialog_context,
                dialog_message_count=dialog_count,
                memory_context=memory_context,
                request_id=request_id,
                request_context=request_context,
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
        result = await orchestrator.handle(payload, _build_user_context(update, context))
    except Exception as exc:
        set_status(context, "error")
        await _handle_exception(update, context, exc)
        return
    await send_result(update, context, result)


@_with_error_handling
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orchestrator = _get_orchestrator(context)
    if not await _guard_access(update, context):
        return
    prompt = " ".join(context.args).strip()
    payload = f"/search {prompt}" if prompt else "/search"
    try:
        result = await orchestrator.handle(payload, _build_user_context(update, context))
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
async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    request_context = get_request_context(context)
    if request_context is None or request_context.env != "dev":
        result = _build_simple_result(
            "Команда недоступна в prod.",
            intent="command.memory",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    memory_store = _get_memory_store(context)
    if memory_store is None:
        result = _build_simple_result(
            "Память не настроена.",
            intent="command.memory",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    chat = update.effective_chat
    if chat is None or getattr(chat, "type", "private") != "private":
        result = _build_simple_result(
            "Команда доступна только в личном чате.",
            intent="command.memory",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    if context.args and context.args[0].strip().lower() == "clear":
        memory_store.clear(chat_id=chat_id, user_id=user_id)
        result = _build_simple_result(
            "Память очищена.",
            intent="command.memory",
            status="ok",
            mode="local",
        )
        await send_result(update, context, result)
        return
    items = memory_store.get_recent(chat_id=chat_id, user_id=user_id, limit=10)
    if not items:
        result = _build_simple_result(
            "Память пуста.",
            intent="command.memory",
            status="ok",
            mode="local",
        )
        await send_result(update, context, result)
        return
    lines = ["Последние записи памяти:"]
    for item in items:
        ts_label = item.ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        preview = item.content.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = preview[:80].rstrip() + "…"
        intent = item.intent or "-"
        lines.append(f"- {ts_label} | {item.role}/{item.kind} | {intent} | {preview}")
    result = _build_simple_result(
        "\n".join(lines),
        intent="command.memory",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    profile_store = _get_profile_store(context)
    if profile_store is None:
        result = _build_simple_result(
            "Профиль не настроен.",
            intent="command.profile",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    profile = profile_store.get(user_id)
    result = _build_simple_result(
        _format_profile(profile),
        intent="command.profile",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def profile_set_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context, bucket="ui"):
        return
    if not _wizards_enabled(context):
        result = refused("Сценарии отключены.", intent="command.profile_set", mode="local")
        await send_result(update, context, result)
        return
    manager = _get_wizard_manager(context)
    if manager is None:
        result = error("Сценарии не настроены.", intent="command.profile_set", mode="local")
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    result = manager.start_profile_set(user_id=user_id, chat_id=chat_id)
    await send_result(update, context, result)


@_with_error_handling
async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    profile_store = _get_profile_store(context)
    if profile_store is None:
        result = _build_simple_result(
            "Профиль не настроен.",
            intent="command.remember",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    note_text = " ".join(context.args).strip() if context.args else ""
    if not note_text:
        result = _build_simple_result(
            "Использование: /remember <текст>.",
            intent="command.remember",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    profile = profile_store.add_note(user_id, note_text)
    note_id = profile.notes[0].id if profile.notes else ""
    result = _build_simple_result(
        f"Запомнил. id: {note_id}",
        intent="command.remember",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    profile_store = _get_profile_store(context)
    if profile_store is None:
        result = _build_simple_result(
            "Профиль не настроен.",
            intent="command.forget",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    key = " ".join(context.args).strip() if context.args else ""
    if not key:
        result = _build_simple_result(
            "Использование: /forget <id|ключ>.",
            intent="command.forget",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    _, removed = profile_store.remove_note(user_id, key)
    if not removed:
        result = _build_simple_result(
            "Не нашёл заметку для удаления.",
            intent="command.forget",
            status="ok",
            mode="local",
        )
        await send_result(update, context, result)
        return
    result = _build_simple_result(
        "Удалил.",
        intent="command.forget",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    actions_store = _get_actions_log_store(context)
    if actions_store is None:
        result = _build_simple_result(
            "История действий не настроена.",
            intent="command.history",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    entries = actions_store.list_recent(user_id=user_id, limit=10)
    result = _build_simple_result(
        _format_actions_history(entries),
        intent="command.history",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


@_with_error_handling
async def history_search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    actions_store = _get_actions_log_store(context)
    if actions_store is None:
        result = _build_simple_result(
            "История действий не настроена.",
            intent="command.history_search",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        result = _build_simple_result(
            "Использование: /history_find <запрос|type:...>.",
            intent="command.history_search",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    entries = actions_store.search(user_id=user_id, query=query, limit=10)
    result = _build_simple_result(
        _format_actions_history(entries),
        intent="command.history_search",
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
    await _send_reply_keyboard_remove(update, context)
    user_id = update.effective_user.id if update.effective_user else 0
    result = ok(
        "Выбери раздел:",
        intent="command.menu",
        mode="local",
        actions=_build_menu_actions(context, user_id=user_id),
    )
    await send_result(update, context, result)


@_with_error_handling
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context, bucket="ui"):
        return
    if not _wizards_enabled(context):
        result = refused("Сценарии отключены.", intent="wizard.cancel", mode="local")
        await send_result(update, context, result)
        return
    manager = _get_wizard_manager(context)
    if manager is None:
        result = error("Сценарии не настроены.", intent="wizard.cancel", mode="local")
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    result = manager.cancel(user_id=user_id, chat_id=chat_id)
    await send_result(update, context, result)


async def _handle_menu_section(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    section: str,
    user_id: int,
    chat_id: int,
) -> OrchestratorResult:
    if context is None:
        text_map = {
            "home": "Выбери раздел:",
            "chat": "Пиши сообщением — отвечу. Можно вернуться в меню.",
            "calc": "Введи выражение (например: 12*(5+3)).",
            "calendar": "Календарь: добавить/посмотреть/удалить события.",
            "reminders": "Напоминания: создать/список/удалить.",
            "settings": "Настройки режимов и поведения.",
            "search": "Ищу в интернете и даю ссылки на источники. Введи запрос командой /search <запрос>.",
            "images": "Опиши картинку — сгенерирую.",
            "image_examples": "Примеры: слон в космосе, кот в стиле пиксель-арт.",
        }
        if section not in text_map:
            return refused(
                "Раздел меню недоступен.",
                intent="menu.unknown",
                mode="local",
                actions=[_menu_action()],
            )
        actions = (
            menu.build_menu_actions(facts_enabled=False, enable_menu=True)
            if section == "home"
            else [_menu_action()]
        )
        return ok(text_map[section], intent=f"menu.section.{section}", mode="local", actions=actions)
    orchestrator = _get_orchestrator(context)
    facts_enabled = bool(user_id) and orchestrator.is_facts_only(user_id)
    facts_command = "/facts_off" if facts_enabled else "/facts_on"
    dialog_memory = _get_dialog_memory(context)
    context_enabled = False
    if dialog_memory is not None and user_id:
        context_enabled = await dialog_memory.is_enabled(user_id)
    if section == "chat":
        actions = [
            Action(
                id="chat.facts",
                label="📌 Режим фактов",
                payload={"op": "run_command", "command": facts_command, "args": ""},
            ),
            _menu_action(),
        ]
        if dialog_memory is not None:
            actions.insert(
                0,
                Action(
                    id="chat.context_clear",
                    label="🧹 Очистить контекст",
                    payload={"op": "run_command", "command": "/context_clear", "args": ""},
                ),
            )
        return ok(
            "Пиши сообщением — отвечу. Можно вернуться в меню.",
            intent="menu.chat",
            mode="local",
            actions=actions,
        )
    if section == "home":
        return ok(
            "Выбери раздел:",
            intent="menu.home",
            mode="local",
            actions=_build_menu_actions(context, user_id=user_id),
        )
    if section == "calc":
        return ok(
            "Введи выражение (например: 12*(5+3)).",
            intent="menu.calc",
            mode="local",
            actions=[
                Action(
                    id="calc.run",
                    label="🧮 Посчитать",
                    payload={"op": "run_command", "command": "/calc", "args": ""},
                ),
                Action(
                    id="calc.examples",
                    label="ℹ️ Примеры",
                    payload={"op": "menu_section", "section": "calc_examples"},
                ),
                _menu_action(),
            ],
        )
    if section == "calc_examples":
        return ok(
            "Примеры:\n• 12*(5+3)\n• 100/4\n• (7+9)*2",
            intent="menu.calc.examples",
            mode="local",
            actions=[_menu_action()],
        )
    if section == "calendar":
        return ok(
            "Календарь: события (добавить/список/удалить).",
            intent="menu.calendar",
            mode="local",
            actions=[
                Action(
                    id="utility_calendar.add",
                    label="➕ Добавить",
                    payload={"op": "wizard_start", "wizard_id": wizard.WIZARD_CALENDAR_ADD},
                ),
                Action(
                    id="utility_calendar.add_nlp",
                    label="✍️ Одним сообщением",
                    payload={"op": "calendar.nlp.start"},
                ),
                Action(
                    id="calendar.list",
                    label="📋 Список",
                    payload={"op": "calendar.list"},
                ),
                _menu_action(),
            ],
        )
    if section == "reminders":
        return ok(
            "Напоминания: отдельные напоминания (создать/список/управление).",
            intent="menu.reminders",
            mode="local",
            actions=[
                Action(
                    id="utility_reminders.create",
                    label="➕ Создать",
                    payload={"op": "wizard_start", "wizard_id": wizard.WIZARD_REMINDER_CREATE},
                ),
                Action(
                    id="utility_reminders.list",
                    label="📋 Список",
                    payload={"op": "reminder.list", "limit": 10},
                ),
                _menu_action(),
            ],
        )
    if section == "settings":
        caldav_status = "подключён" if _caldav_configured(context) else "не подключён"
        return ok(
            f"Настройки режимов и поведения.\nCalDAV: {caldav_status}.",
            intent="menu.settings",
            mode="local",
            actions=[
                Action(
                    id="settings.caldav",
                    label="📅 CalDAV → Подключить",
                    payload={"op": "caldav_settings"},
                ),
                Action(
                    id="settings.facts",
                    label=f"📌 Факты {'off' if facts_enabled else 'on'}",
                    payload={"op": "run_command", "command": facts_command, "args": ""},
                ),
                Action(
                    id="settings.context",
                    label=f"🧠 Контекст {'off' if context_enabled else 'on'}",
                    payload={
                        "op": "run_command",
                        "command": "/context_off" if context_enabled else "/context_on",
                        "args": "",
                    },
                ),
                Action(
                    id="settings.profile",
                    label="👤 Профиль",
                    payload={"op": "run_command", "command": "/profile", "args": ""},
                ),
                Action(
                    id="settings.profile_set",
                    label="🛠 Профиль: настроить",
                    payload={"op": "run_command", "command": "/profile_set", "args": ""},
                ),
                Action(
                    id="settings.history",
                    label="📜 История",
                    payload={"op": "run_command", "command": "/history", "args": ""},
                ),
                _menu_action(),
            ],
        )
    if section == "search":
        return ok(
            "Ищу в интернете и даю ссылки на источники. Введи запрос командой /search <запрос>.",
            intent="menu.search",
            mode="local",
            actions=[
                Action(
                    id="search.new",
                    label="🔎 Новый поиск",
                    payload={"op": "run_command", "command": "/search", "args": ""},
                ),
                Action(
                    id="search.facts",
                    label="📌 Режим фактов",
                    payload={"op": "run_command", "command": facts_command, "args": ""},
                ),
                _menu_action(),
            ],
        )
    if section == "images":
        return ok(
            "Опиши картинку — сгенерирую.",
            intent="menu.images",
            mode="local",
            actions=[
                Action(
                    id="images.generate",
                    label="🖼 Сгенерировать",
                    payload={"op": "run_command", "command": "/image", "args": ""},
                ),
                Action(
                    id="images.examples",
                    label="ℹ️ Примеры",
                    payload={"op": "menu_section", "section": "image_examples"},
                ),
                _menu_action(),
            ],
        )
    if section == "image_examples":
        return ok(
            "Примеры:\n• Слон в космосе\n• Замок на берегу моря\n• Робот в стиле пиксель-арт",
            intent="menu.images.examples",
            mode="local",
            actions=[_menu_action()],
        )
    return refused(
        "Раздел меню недоступен.",
        intent="menu.unknown",
        mode="local",
        actions=[_menu_action()],
    )


def _parse_static_callback(data: str) -> tuple[str, dict[str, object], str] | None:
    if not data.startswith("cb:"):
        return None
    parts = data.split(":")
    if len(parts) < 3:
        return None
    _prefix, domain, action, *rest = parts
    if domain == "menu":
        if action == "open":
            return "menu_open", {}, "callback.menu.open"
        if action == "cancel":
            return "menu_cancel", {}, "callback.menu.cancel"
        if action == "section" and rest:
            section = rest[0]
            return "menu_section", {"section": section}, f"callback.menu.section.{section}"
        return None
    if domain == "wiz":
        wizard_ops = {
            "confirm": "wizard_confirm",
            "cancel": "wizard_cancel",
            "edit": "wizard_edit",
            "continue": "wizard_continue",
            "restart": "wizard_restart",
            "start": "wizard_start",
        }
        op = wizard_ops.get(action)
        if op is None:
            return None
        payload: dict[str, object] = {}
        if action in {"continue", "restart", "start"}:
            if not rest or not rest[0]:
                return None
            payload["wizard_id"] = rest[0]
        elif rest and rest[0]:
            payload["wizard_id"] = rest[0]
        return op, payload, f"callback.wiz.{action}"
    return None


def _normalize_callback_intent(intent: str) -> str:
    if intent == "calendar.list":
        return "utility_calendar.list"
    return intent


@_with_error_handling
async def static_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await _safe_answer_callback(query)
    if not await _guard_access(update, context, bucket="ui"):
        return
    data = query.data or ""
    set_input_text(context, data)
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        LOGGER.warning("Callback missing chat_id: user_id=%s data=%r", user_id, data)
        result = refused(
            "Не удалось обработать кнопку. Открой меню.",
            intent="callback.missing_chat",
            mode="local",
        )
        await send_result(update, context, result)
        return
    parsed = _parse_static_callback(data)
    if parsed is None:
        result = refused(
            "Действие недоступно.",
            intent="callback.invalid",
            mode="local",
            debug={"reason": "invalid_static_callback"},
        )
        await send_result(update, context, result)
        return
    op, payload, intent = parsed
    set_input_text(context, f"<callback:{intent}>")
    result = await _dispatch_action_payload(
        update,
        context,
        op=op,
        payload=payload,
        intent=intent,
    )
    await send_result(update, context, result)


@_with_error_handling
async def action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await _safe_answer_callback(query)
    if not await _guard_access(update, context, bucket="ui"):
        return
    data = query.data or ""
    set_input_text(context, data)
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        LOGGER.warning("Callback missing chat_id: user_id=%s data=%r", user_id, data)
        result = refused(
            "Не удалось обработать кнопку. Открой меню.",
            intent="callback.missing_chat",
            mode="local",
        )
        await send_result(update, context, result)
        return
    action_id = parse_callback_token(data)
    LOGGER.info("Callback: user_id=%s data=%r action_id=%s", user_id, data, action_id)
    if action_id is None:
        result = refused(
            "Действие недоступно.",
            intent="ui.action",
            mode="local",
            debug={"reason": "invalid_callback"},
        )
        await send_result(update, context, result)
        return
    store = _get_action_store(context)
    lookup = store.lookup_action(user_id=user_id, chat_id=chat_id, action_id=action_id)
    if lookup.action is None:
        if lookup.status == "expired":
            LOGGER.warning(
                "Action expired: user_id=%s action_id=%s age=%.1fs ttl=%.1fs",
                user_id,
                action_id,
                lookup.age_seconds or 0.0,
                lookup.ttl_seconds or 0.0,
            )
        else:
            LOGGER.warning(
                "Action not found/expired: user_id=%s action_id=%s status=%s",
                user_id,
                action_id,
                lookup.status,
            )
        LOGGER.info("Callback dispatch: action_id=%s intent=%s", action_id, "-")
        result = refused(
            "Кнопка устарела. Открой меню заново.",
            intent="callback.expired",
            mode="local",
            debug={"reason": "action_missing"},
        )
        await send_result(update, context, result)
        return
    stored = lookup.action
    normalized_intent = _normalize_callback_intent(stored.intent)
    LOGGER.info("Callback dispatch: action_id=%s intent=%s", action_id, normalized_intent)
    set_input_text(context, f"<callback:{normalized_intent}>")
    result = await _dispatch_action_payload(
        update,
        context,
        op=stored.payload.get("op"),
        payload=stored.payload,
        intent=normalized_intent,
    )
    if (
        isinstance(result, OrchestratorResult)
        and normalized_intent != stored.intent
        and result.intent != normalized_intent
    ):
        result = replace(result, intent=normalized_intent)
    public_result = normalize_to_orchestrator_result(result)
    LOGGER.info(
        "Orchestrator result: status=%s mode=%s intent=%s sources=%d actions=%d attachments=%d",
        public_result.status,
        public_result.mode,
        public_result.intent,
        len(public_result.sources),
        len(public_result.actions),
        len(public_result.attachments),
    )
    await send_result(update, context, public_result)


async def _dispatch_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    stored: StoredAction,
) -> OrchestratorResult:
    return await _dispatch_action_payload(
        update,
        context,
        op=stored.payload.get("op"),
        payload=stored.payload,
        intent=stored.intent,
    )


async def _dispatch_action_payload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    op: object,
    payload: dict[str, object],
    intent: str,
) -> OrchestratorResult:
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        LOGGER.warning("Dispatch action missing chat_id: user_id=%s intent=%s", user_id, intent)
        return refused(
            "Не удалось обработать кнопку. Открой меню.",
            intent="callback.missing_chat",
            mode="local",
        )
    request_context = get_request_context(context)
    op_value = op if isinstance(op, str) else ""
    if op_value == "menu_open":
        await _send_reply_keyboard_remove(update, context)
        user_id = update.effective_user.id if update.effective_user else 0
        return ok("Выбери раздел:", intent="menu.open", mode="local", actions=_build_menu_actions(context, user_id=user_id))
    if op_value == "menu_cancel":
        await _send_reply_keyboard_remove(update, context, text="Ок")
        return ok("Ок", intent="menu.cancel", mode="local")
    if op_value == "trace_last":
        if _is_group_chat(update):
            return refused("Команда /trace недоступна в группах.", intent="command.trace", mode="local")
        return _handle_trace_request(
            context,
            user_id=user_id,
            chat_id=chat_id,
            use_last=True,
        )
    if op_value == "last_state_action":
        action = payload.get("action")
        ref = payload.get("ref")
        action_value = action if isinstance(action, str) else "resolve"
        ref_value = ref if isinstance(ref, str) else ""
        last_state_store = _get_last_state_store(context)
        last_state = (
            last_state_store.get_state(chat_id=chat_id, user_id=user_id) if last_state_store else None
        )
        if last_state is None:
            return _build_resolution_fallback(action_value, reason="missing_last_state")
        if action_value == "move_tomorrow" and ref_value == "event":
            if last_state.last_event_id:
                return await _handle_event_move_tomorrow(
                    context,
                    event_id=last_state.last_event_id,
                    user_id=user_id,
                    chat_id=chat_id,
                )
            return _build_resolution_fallback(action_value, reason="missing_last_event")
        if action_value == "cancel":
            if ref_value == "reminder":
                if last_state.last_reminder_id:
                    return await _handle_reminder_delete(
                        context,
                        reminder_id=last_state.last_reminder_id,
                        user_id=user_id,
                        chat_id=chat_id,
                    )
                return _build_resolution_fallback(action_value, reason="missing_last_reminder")
            if ref_value == "event":
                if last_state.last_event_id:
                    return await _handle_event_delete(
                        context,
                        event_id=last_state.last_event_id,
                        user_id=user_id,
                    )
                return _build_resolution_fallback(action_value, reason="missing_last_event")
        if action_value == "repeat_search" and ref_value == "search":
            if last_state.last_query:
                orchestrator = _get_orchestrator(context)
                return await orchestrator.handle(
                    f"/search {last_state.last_query}",
                    _build_user_context(update, context),
                    request_context=request_context,
                )
            return _build_resolution_fallback(action_value, reason="missing_last_query")
        if action_value == "move":
            return ok(
                "Укажи новую дату и время для переноса. Например: завтра 10:00.",
                intent="memory.resolve",
                mode="local",
                actions=[_menu_action()],
            )
        return _build_resolution_fallback(action_value, reason="unsupported_action")
    if op_value == "menu_section":
        section = payload.get("section")
        if not isinstance(section, str):
            return error(
                "Некорректный раздел меню.",
                intent="menu.section",
                mode="local",
                debug={"reason": "invalid_section"},
            )
        return await _handle_menu_section(context, section=section, user_id=user_id, chat_id=chat_id)
    if op_value == "document.summary":
        doc_id = payload.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id:
            return error("Некорректные данные действия.", intent="document.summary", mode="local")
        return await _handle_document_summary(context, user_id=user_id, chat_id=chat_id, doc_id=doc_id)
    if op_value == "document.qa":
        doc_id = payload.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id:
            return error("Некорректные данные действия.", intent="document.qa", mode="local")
        document_store = _get_document_store(context)
        if document_store is None:
            return error("Хранилище документов недоступно.", intent="document.qa", mode="local")
        session = document_store.set_state(doc_id=doc_id, state="qa_mode")
        if session is None:
            return refused("Сначала пришлите файл.", intent="document.qa", mode="local")
        return ok(
            "Задайте вопрос по документу.",
            intent="document.qa.start",
            mode="local",
            actions=_document_qa_actions(doc_id),
        )
    if op_value == "document.qa_exit":
        doc_id = payload.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id:
            return error("Некорректные данные действия.", intent="document.qa.exit", mode="local")
        document_store = _get_document_store(context)
        if document_store is None:
            return error("Хранилище документов недоступно.", intent="document.qa.exit", mode="local")
        session = document_store.set_state(doc_id=doc_id, state="action_select")
        if session is None:
            return refused("Сначала пришлите файл.", intent="document.qa.exit", mode="local")
        return ok(
            "Вы вышли из Q&A. Что сделать?",
            intent="document.qa.exit",
            mode="local",
            actions=_document_actions(doc_id),
        )
    if op_value == "document.close":
        document_store = _get_document_store(context)
        if document_store is None:
            return error("Хранилище документов недоступно.", intent="document.close", mode="local")
        closed = document_store.close_active(user_id=user_id, chat_id=chat_id)
        if closed is None:
            return refused("Нет активного документа.", intent="document.close", mode="local")
        return ok("Документ закрыт.", intent="document.close", mode="local")
    if op_value == "wizard.resume":
        manager = _get_wizard_manager(context)
        if manager is None:
            return error("Сценарии не настроены.", intent="wizard.missing", mode="local")
        result = await manager.handle_action(
            user_id=user_id,
            chat_id=chat_id,
            op="wizard_continue",
            payload={},
        )
        if result is None:
            return refused("Нет активного сценария.", intent="wizard.resume", mode="local")
        return result
    if op_value == "wizard.restart":
        manager = _get_wizard_manager(context)
        if manager is None:
            return error("Сценарии не настроены.", intent="wizard.missing", mode="local")
        target = payload.get("target")
        manager.cancel(user_id=user_id, chat_id=chat_id)
        wizard_id = _map_wizard_target(target if isinstance(target, str) else None)
        if wizard_id is None:
            return refused("Не удалось начать заново, открой /menu.", intent="wizard.restart", mode="local")
        result = await manager.handle_action(
            user_id=user_id,
            chat_id=chat_id,
            op="wizard_start",
            payload={"wizard_id": wizard_id},
        )
        if result is None:
            return refused("Не удалось начать заново, открой /menu.", intent="wizard.restart", mode="local")
        return result
    if op_value == "wizard.cancel":
        manager = _get_wizard_manager(context)
        if manager is None:
            return error("Сценарии не настроены.", intent="wizard.missing", mode="local")
        return manager.cancel(user_id=user_id, chat_id=chat_id)
    if op_value in {
        "wizard_start",
        "wizard_continue",
        "wizard_restart",
        "wizard_cancel",
        "wizard_confirm",
        "wizard_edit",
    }:
        if not _wizards_enabled(context):
            return refused(
                "Сценарии отключены.",
                intent="wizard.disabled",
                mode="local",
            )
        manager = _get_wizard_manager(context)
        if manager is None:
            return error(
                "Сценарии не настроены.",
                intent="wizard.missing",
                mode="local",
            )
        result = await manager.handle_action(
            user_id=user_id,
            chat_id=chat_id,
            op=op_value,
            payload=payload,
        )
        if result is None:
            return refused(
                "Сценарий не активен.",
                intent="wizard.inactive",
                mode="local",
            )
        return result
    if op_value == "calendar.nlp.start":
        draft_store = _get_draft_store(context)
        if draft_store is not None:
            draft_store.set_force_nlp(chat_id=chat_id, user_id=user_id, enabled=True)
        return ok(
            "Напиши событие одной фразой.",
            intent="calendar.nlp.start",
            mode="local",
            actions=[_menu_action()],
        )
    if op_value == "calendar.create_confirm":
        draft_id = payload.get("draft_id")
        if not isinstance(draft_id, str) or not draft_id:
            return error("Некорректные данные действия.", intent="calendar.nlp.confirm", mode="local")
        return await _handle_calendar_draft_confirm(
            context,
            user_id=user_id,
            chat_id=chat_id,
            draft_id=draft_id,
        )
    if op_value == "calendar.create_edit":
        draft_id = payload.get("draft_id")
        if not isinstance(draft_id, str) or not draft_id:
            return error("Некорректные данные действия.", intent="calendar.nlp.edit", mode="local")
        return await _handle_calendar_draft_edit(
            context,
            user_id=user_id,
            chat_id=chat_id,
            draft_id=draft_id,
        )
    if op_value == "calendar.create_cancel":
        draft_id = payload.get("draft_id")
        if not isinstance(draft_id, str) or not draft_id:
            return ok("Ок, отменил.", intent="calendar.nlp.cancel", mode="local")
        return await _handle_calendar_draft_cancel(
            context,
            user_id=user_id,
            chat_id=chat_id,
            draft_id=draft_id,
        )
    if op_value == "calendar.add":
        if not _wizards_enabled(context):
            return refused("Сценарии отключены.", intent="wizard.disabled", mode="local")
        manager = _get_wizard_manager(context)
        if manager is None:
            return error("Сценарии не настроены.", intent="wizard.missing", mode="local")
        result = await manager.handle_action(
            user_id=user_id,
            chat_id=chat_id,
            op="wizard_start",
            payload={"wizard_id": wizard.WIZARD_CALENDAR_ADD},
        )
        return result if result is not None else refused("Сценарий не активен.", intent="wizard.inactive", mode="local")
    if op_value == "calendar.list":
        days = payload.get("days", 7)
        days_value = days if isinstance(days, int) else 7
        start = datetime.now(tz=calendar_store.BOT_TZ)
        end = start + timedelta(days=max(1, days_value))
        return await _build_calendar_list_result(
            start,
            end,
            user_id=user_id,
            chat_id=chat_id,
            intent="utility_calendar.list",
        )
    if op_value == "calendar.delete":
        event_id = payload.get("event_id")
        scope = normalize_scope(payload.get("scope"))
        instance_raw = payload.get("instance_dt")
        instance_dt = None
        if isinstance(instance_raw, str):
            try:
                instance_dt = datetime.fromisoformat(instance_raw)
            except ValueError:
                instance_dt = None
        if not isinstance(event_id, str) or not event_id:
            return error(
                "Некорректные данные действия.",
                intent="utility_calendar.delete",
                mode="local",
                debug={"reason": "invalid_event_id"},
            )
        event = await calendar_store.get_event(event_id)
        if event is None:
            return refused(
                "Событие не найдено.",
                intent="utility_calendar.delete",
                mode="local",
            )
        if event.rrule and scope is None:
            return ok(
                "Это повторяющееся событие. Что изменить?",
                intent="utility_calendar.delete",
                mode="local",
                actions=_build_recurrence_scope_actions(
                    "calendar.delete",
                    event_id=event_id,
                    instance_dt=instance_dt or event.dt,
                ),
            )
        deleted = await delete_event(
            event_id,
            scope=scope or RecurrenceScope.ALL,
            instance_dt=instance_dt or event.dt,
            intent="utility_calendar.delete",
            user_id=user_id,
            request_context=request_context,
            circuit_breakers=_get_circuit_breakers(context),
            retry_policy=_get_retry_policy(context),
            timeouts=_get_timeouts(context),
        )
        if deleted.status != "ok":
            if deleted.status == "refused":
                return refused(
                    "Кнопка устарела, открой список заново.",
                    intent="utility_calendar.delete",
                    mode="local",
                )
            return replace(
                deleted,
                mode="local",
                intent="utility_calendar.delete",
                actions=_calendar_list_controls_actions(),
            )
        return replace(
            deleted,
            mode="local",
            intent="utility_calendar.delete",
            actions=_calendar_list_controls_actions(),
        )
    if op_value == "calendar.move_tomorrow":
        event_id = payload.get("event_id")
        scope = normalize_scope(payload.get("scope"))
        instance_raw = payload.get("instance_dt")
        instance_dt = None
        if isinstance(instance_raw, str):
            try:
                instance_dt = datetime.fromisoformat(instance_raw)
            except ValueError:
                instance_dt = None
        if not isinstance(event_id, str) or not event_id:
            return error(
                "Некорректные данные действия.",
                intent="utility_calendar.move",
                mode="local",
                debug={"reason": "invalid_event_id"},
            )
        return await _handle_event_move_tomorrow(
            context,
            event_id=event_id,
            user_id=user_id,
            chat_id=chat_id,
            scope=scope,
            instance_dt=instance_dt,
        )
    if op_value == "calendar.move_phrase":
        event_id = payload.get("event_id")
        scope = normalize_scope(payload.get("scope"))
        instance_raw = payload.get("instance_dt")
        instance_dt = None
        if isinstance(instance_raw, str):
            try:
                instance_dt = datetime.fromisoformat(instance_raw)
            except ValueError:
                instance_dt = None
        if not isinstance(event_id, str) or not event_id:
            return error(
                "Некорректные данные действия.",
                intent="utility_calendar.move",
                mode="local",
                debug={"reason": "invalid_event_id"},
            )
        return await _handle_event_move_phrase(
            context,
            event_id=event_id,
            text=str(payload.get("text") or ""),
            user_id=user_id,
            chat_id=chat_id,
            scope=scope,
            instance_dt=instance_dt,
        )
    if op_value == "reminder.create":
        if not _wizards_enabled(context):
            return refused("Сценарии отключены.", intent="wizard.disabled", mode="local")
        manager = _get_wizard_manager(context)
        if manager is None:
            return error("Сценарии не настроены.", intent="wizard.missing", mode="local")
        result = await manager.handle_action(
            user_id=user_id,
            chat_id=chat_id,
            op="wizard_start",
            payload={"wizard_id": wizard.WIZARD_REMINDER_CREATE},
        )
        return result if result is not None else refused("Сценарий не активен.", intent="wizard.inactive", mode="local")
    if op_value == "reminder.list":
        limit = payload.get("limit", 10)
        limit_value = limit if isinstance(limit, int) else 10
        return await _handle_reminders_list(
            context,
            user_id=user_id,
            chat_id=chat_id,
            limit=max(1, limit_value),
            intent="utility_reminders.list",
        )
    if op_value == "reminder.delete_confirm":
        reminder_id = payload.get("reminder_id") or payload.get("id")
        if not isinstance(reminder_id, str) or not reminder_id:
            return error(
                "Некорректные данные действия.",
                intent="utility_reminders.delete",
                mode="local",
                debug={"reason": "invalid_reminder_id"},
            )
        reminder = await calendar_store.get_reminder(reminder_id)
        if reminder is None or reminder.user_id != user_id or reminder.chat_id != chat_id:
            return refused(
                "Напоминание не найдено.",
                intent="utility_reminders.delete",
                mode="local",
            )
        return ok(
            f"Удалить напоминание: {reminder.text}?",
            intent="utility_reminders.delete",
            mode="local",
            actions=_reminder_delete_confirm_actions(reminder_id),
        )
    if op_value == "reminder.delete_confirmed":
        reminder_id = payload.get("reminder_id") or payload.get("id")
        if not isinstance(reminder_id, str) or not reminder_id:
            return error(
                "Некорректные данные действия.",
                intent="utility_reminders.delete",
                mode="local",
                debug={"reason": "invalid_reminder_id"},
            )
        result = await _handle_reminder_delete(
            context,
            reminder_id=reminder_id,
            user_id=user_id,
            chat_id=chat_id,
        )
        return replace(
            result,
            mode="local",
            intent="utility_reminders.delete",
            actions=_reminder_list_controls_actions(),
        )
    if op_value == "reminder.delete":
        reminder_id = payload.get("reminder_id") or payload.get("id")
        if not isinstance(reminder_id, str) or not reminder_id:
            return error(
                "Некорректные данные действия.",
                intent="utility_reminders.delete",
                mode="local",
                debug={"reason": "invalid_reminder_id"},
            )
        result = await _handle_reminder_delete(
            context,
            reminder_id=reminder_id,
            user_id=user_id,
            chat_id=chat_id,
        )
        return replace(
            result,
            mode="local",
            intent="utility_reminders.delete",
            actions=_reminder_list_controls_actions(),
        )
    if op_value == "reminder.disable":
        reminder_id = payload.get("reminder_id") or payload.get("id")
        if not isinstance(reminder_id, str) or not reminder_id:
            return error(
                "Некорректные данные действия.",
                intent="utility_reminders.disable",
                mode="local",
                debug={"reason": "invalid_reminder_id"},
            )
        result = await _handle_reminder_disable(
            context,
            reminder_id=reminder_id,
            user_id=user_id,
            chat_id=chat_id,
        )
        return replace(result, mode="local", intent="utility_reminders.disable")
    if op_value == "run_command":
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
    if op_value == "reminder_off":
        reminder_id = payload.get("reminder_id") or payload.get("id")
        if not isinstance(reminder_id, str) or not reminder_id:
            return error(
                "Некорректные данные действия.",
                intent="ui.action",
                mode="local",
                debug={"reason": "invalid_reminder_id"},
            )
        return await _handle_reminder_off(context, user_id=user_id, reminder_id=reminder_id)
    if op_value == "reminder_on":
        event_id = payload.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            return error(
                "Некорректные данные действия.",
                intent="ui.action",
                mode="local",
                debug={"reason": "invalid_event_id"},
            )
        return await _handle_reminder_on(context, user_id=user_id, event_id=event_id)
    if op_value == "reminders_list":
        limit = payload.get("limit", 5)
        limit_value = limit if isinstance(limit, int) else 5
        return await _handle_reminders_list(
            context,
            user_id=user_id,
            chat_id=chat_id,
            limit=max(1, limit_value),
            intent=intent,
        )
    if op_value == "reminder_snooze":
        reminder_id = payload.get("reminder_id") or payload.get("id")
        minutes = payload.get("minutes", 10)
        base_trigger_at = payload.get("base_trigger_at")
        if not isinstance(reminder_id, str) or not reminder_id:
            return error(
                "Некорректные данные действия.",
                intent="ui.action",
                mode="local",
                debug={"reason": "invalid_reminder_id"},
            )
        minutes_value = minutes if isinstance(minutes, int) else 10
        base_value = base_trigger_at if isinstance(base_trigger_at, str) else None
        return await _handle_reminder_snooze(
            context,
            user_id=user_id,
            reminder_id=reminder_id,
            minutes=minutes_value,
            base_trigger_at=base_value,
        )
    if op_value == "reminder_reschedule":
        reminder_id = payload.get("reminder_id") or payload.get("id")
        if not isinstance(reminder_id, str) or not reminder_id:
            return error(
                "Некорректные данные действия.",
                intent="ui.action",
                mode="local",
                debug={"reason": "invalid_reminder_id"},
            )
        return await _handle_reminder_reschedule_start(
            context,
            user_id=user_id,
            chat_id=chat_id,
            reminder_id=reminder_id,
        )
    if op_value in {"reminder_delete", "reminder_disable"}:
        reminder_id = payload.get("reminder_id") or payload.get("id")
        if not isinstance(reminder_id, str) or not reminder_id:
            return error(
                "Некорректные данные действия.",
                intent="ui.action",
                mode="local",
                debug={"reason": "invalid_reminder_id"},
            )
        if op_value == "reminder_delete":
            return await _handle_reminder_delete(
                context,
                reminder_id=reminder_id,
                user_id=user_id,
                chat_id=chat_id,
            )
        return await _handle_reminder_disable(
            context,
            reminder_id=reminder_id,
            user_id=user_id,
            chat_id=chat_id,
        )
    if op_value == "reminder_add_offset":
        event_id = payload.get("event_id")
        minutes = payload.get("minutes", 10)
        if not isinstance(event_id, str) or not event_id:
            return error(
                "Некорректные данные действия.",
                intent="ui.action",
                mode="local",
                debug={"reason": "invalid_event_id"},
            )
        minutes_value = minutes if isinstance(minutes, int) else 10
        return await _handle_reminder_add_offset(context, event_id=event_id, minutes=minutes_value)
    if op_value == "caldav_settings":
        return await _handle_caldav_settings(context, user_id=user_id)
    if op_value == "caldav_check":
        return await _handle_caldav_check(context)
    orchestrator = _get_orchestrator(context)
    if intent == "task.execute":
        task_name = payload.get("name")
        task_payload = payload.get("payload")
        if not isinstance(task_name, str) or not isinstance(task_payload, str):
            return error(
                "Некорректные данные действия.",
                intent="ui.action",
                mode="local",
                debug={"reason": "invalid_task_payload"},
            )
        return orchestrator.execute_task(
            user_id,
            task_name,
            task_payload,
            request_context=get_request_context(context),
        )
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        return await orchestrator.handle(text, _build_user_context(update, context))
    return refused(
        "Действие не поддерживается.",
        intent="ui.action",
        mode="local",
        debug={"reason": "unknown_action", "action_id": intent},
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
        await _send_reply_keyboard_remove(update, context)
        user_id = update.effective_user.id if update.effective_user else 0
        return ok("Выбери раздел:", intent="menu.open", mode="local", actions=_build_menu_actions(context, user_id=user_id))
    if normalized == "/calc":
        return ok("Calc: /calc <выражение>.", intent="menu.calc", mode="local")
    if normalized == "/calendar":
        return ok(
            "Calendar: /calendar add YYYY-MM-DD HH:MM <title> (или DD.MM.YYYY HH:MM) | list [YYYY-MM-DD YYYY-MM-DD] | today | week | del <id> | debug_due.",
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
    if normalized == "/config":
        request_context = get_request_context(context)
        if request_context is None or request_context.env != "dev":
            return refused("Команда недоступна в prod.", intent="command.config", mode="local")
        return ok(_build_config_message(context), intent="command.config", mode="local")
    if normalized == "/summary":
        return ok(
            "Summary: /summary <текст> или summary: <текст>.",
            intent="menu.summary",
            mode="local",
        )
    if normalized == "/image":
        return refused(
            "Укажите описание изображения. Пример: /image Слон в космосе",
            intent="command.image",
            mode="local",
        )
    if normalized == "/search":
        query = args.strip()
        if not query:
            return refused("Укажи запрос: /search <текст>", intent="menu.search", mode="local")
        return await orchestrator.handle(f"/search {query}", _build_user_context(update, context))
    if normalized == "/reminders":
        user_id = update.effective_user.id if update.effective_user else 0
        chat_id = update.effective_chat.id if update.effective_chat else 0
        return await _handle_reminders_list(
            context,
            user_id=user_id,
            chat_id=chat_id,
            limit=5,
            intent="utility_reminders.list",
        )
    if normalized == "/trace":
        if _is_group_chat(update):
            return refused("Команда /trace недоступна в группах.", intent="command.trace", mode="local")
        user_id = update.effective_user.id if update.effective_user else 0
        chat_id = update.effective_chat.id if update.effective_chat else 0
        if not user_id or not chat_id:
            return refused("Не удалось определить пользователя.", intent="command.trace", mode="local")
        return _handle_trace_request(
            context,
            user_id=user_id,
            chat_id=chat_id,
            correlation_id=args.strip() or None,
        )
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
    if normalized in {"/context_on", "/context_off", "/context_clear"}:
        dialog_memory = _get_dialog_memory(context)
        if dialog_memory is None:
            return refused("Контекст диалога не настроен.", intent="menu.context", mode="local")
        user_id = update.effective_user.id if update.effective_user else 0
        if normalized == "/context_clear":
            chat_id = update.effective_chat.id if update.effective_chat else 0
            await dialog_memory.clear(user_id, chat_id)
            return ok("Контекст очищен.", intent="menu.context", mode="local")
        enabled = normalized == "/context_on"
        await dialog_memory.set_enabled(user_id, enabled)
        text = "Контекст включён." if enabled else "Контекст выключён."
        return ok(text, intent="menu.context", mode="local")
    return refused(
        "Неизвестная команда. Открой меню.",
        intent="ui.action",
        mode="local",
        debug={"command": command, "args": args},
    )


async def _handle_reminders_list(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    chat_id: int,
    limit: int = 10,
    intent: str = "utility_reminders.list",
) -> OrchestratorResult:
    now = datetime.now(tz=calendar_store.BOT_TZ)
    return await _build_reminders_list_result(
        now,
        user_id=user_id,
        chat_id=chat_id,
        limit=limit,
        intent=intent,
    )


async def _handle_reminder_snooze(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    reminder_id: str,
    minutes: int,
    base_trigger_at: str | None = None,
) -> OrchestratorResult:
    reminder = await calendar_store.get_reminder(reminder_id)
    if reminder is None:
        return refused(
            f"Напоминание не найдено: {reminder_id}",
            intent="utility_reminders.snooze",
            mode="local",
        )
    offset = max(1, minutes)
    base_dt = None
    if base_trigger_at:
        try:
            base_dt = datetime.fromisoformat(base_trigger_at)
        except ValueError:
            base_dt = None
        if base_dt and base_dt.tzinfo is None:
            base_dt = base_dt.replace(tzinfo=calendar_store.BOT_TZ)
        if base_dt and base_dt.tzinfo is not None:
            base_dt = base_dt.astimezone(calendar_store.BOT_TZ)
    updated = await calendar_store.apply_snooze(reminder_id, minutes=offset, now=datetime.now(tz=calendar_store.BOT_TZ), base_trigger_at=base_dt)
    if updated is None:
        return error(
            "Не удалось отложить напоминание (возможно, уже отключено).",
            intent="utility_reminders.snooze",
            mode="local",
        )
    scheduler = _get_reminder_scheduler(context)
    settings = _get_settings(context)
    if scheduler and settings is not None and settings.reminders_enabled:
        try:
            await scheduler.schedule_reminder(updated)
        except Exception:
            LOGGER.exception("Failed to reschedule reminder: reminder_id=%s", reminder_id)
            return error(
                "Не удалось отложить напоминание.",
                intent="utility_reminders.snooze",
                mode="local",
            )
    LOGGER.info(
        "Reminder snoozed: reminder_id=%s user_id=%s old_trigger_at=%s new_trigger_at=%s",
        reminder_id,
        user_id,
        reminder.trigger_at.isoformat(),
        updated.trigger_at.isoformat(),
    )
    when_label = updated.trigger_at.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
    return ok(
        f"Ок, отложил до {when_label}.",
        intent="utility_reminders.snooze",
        mode="local",
        debug={"refs": {"reminder_id": reminder_id}},
    )


async def _handle_reminder_delete(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    reminder_id: str,
    user_id: int,
    chat_id: int,
) -> OrchestratorResult:
    scheduler = _get_reminder_scheduler(context)
    if scheduler:
        try:
            await scheduler.cancel_reminder(reminder_id)
        except Exception:
            LOGGER.exception("Failed to cancel reminder: reminder_id=%s", reminder_id)
            return error(
                "Не удалось удалить напоминание.",
                intent="utility_reminders.delete",
                mode="local",
            )
    reminder = await calendar_store.get_reminder(reminder_id)
    if reminder is None or reminder.user_id != user_id or reminder.chat_id != chat_id:
        return refused(
            "Напоминание не найдено.",
            intent="utility_reminders.delete",
            mode="local",
        )
    deleted = await calendar_store.delete_reminder(reminder_id)
    if not deleted:
        return refused(
            "Напоминание не найдено.",
            intent="utility_reminders.delete",
            mode="local",
        )
    LOGGER.info("Reminder deleted: reminder_id=%s user_id=%s", reminder_id, reminder.user_id)
    return ok(
        f"Удалено: {reminder_id}",
        intent="utility_reminders.delete",
        mode="local",
        debug={"refs": {"reminder_id": reminder_id}},
    )


async def _handle_reminder_disable(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    reminder_id: str,
    user_id: int,
    chat_id: int,
) -> OrchestratorResult:
    scheduler = _get_reminder_scheduler(context)
    if scheduler:
        try:
            await scheduler.cancel_reminder(reminder_id)
        except Exception:
            LOGGER.exception("Failed to cancel reminder: reminder_id=%s", reminder_id)
            return error(
                "Не удалось отключить напоминание.",
                intent="utility_reminders.disable",
                mode="local",
            )
    reminder = await calendar_store.get_reminder(reminder_id)
    if reminder is None or reminder.user_id != user_id or reminder.chat_id != chat_id:
        return refused(
            "Напоминание не найдено.",
            intent="utility_reminders.disable",
            mode="local",
        )
    if reminder.status == "disabled":
        return ok(
            "Напоминание уже отключено.",
            intent="utility_reminders.disable",
            mode="local",
        )
    updated = await calendar_store.disable_reminder(reminder_id)
    if not updated:
        return error(
            "Не удалось отключить напоминание.",
            intent="utility_reminders.disable",
            mode="local",
        )
    LOGGER.info(
        "Reminder disabled: reminder_id=%s user_id=%s trigger_at=%s",
        reminder_id,
        reminder.user_id,
        reminder.trigger_at.isoformat(),
    )
    return ok(
        "Ок, отключил.",
        intent="utility_reminders.disable",
        mode="local",
        debug={"refs": {"reminder_id": reminder_id}},
    )


async def _handle_event_delete(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    event_id: str,
    scope: RecurrenceScope | str | None = None,
    instance_dt: datetime | None = None,
    user_id: int,
) -> OrchestratorResult:
    event = await calendar_store.get_event(event_id)
    scope_value = normalize_scope(scope)
    if event is not None and event.rrule and scope_value is None:
        return ok(
            "Это повторяющееся событие. Что изменить?",
            intent="utility_calendar.delete",
            mode="local",
            actions=_build_recurrence_scope_actions(
                "calendar.delete",
                event_id=event_id,
                instance_dt=instance_dt or event.dt,
            ),
        )
    request_context = get_request_context(context)
    tool_result = await delete_event(
        event_id,
        scope=scope_value or RecurrenceScope.ALL,
        instance_dt=instance_dt or (event.dt if event else None),
        intent="utility_calendar.delete",
        user_id=user_id,
        request_context=request_context,
        circuit_breakers=_get_circuit_breakers(context),
        retry_policy=_get_retry_policy(context),
        timeouts=_get_timeouts(context),
    )
    reminder_id = tool_result.debug.get("reminder_id") if isinstance(tool_result.debug, dict) else None
    scheduler = _get_reminder_scheduler(context)
    if reminder_id and scheduler:
        try:
            await scheduler.cancel_reminder(reminder_id)
        except Exception:
            LOGGER.exception("Failed to cancel reminder: reminder_id=%s", reminder_id)
    return replace(tool_result, mode="local", intent="utility_calendar.delete")


async def _handle_event_move_tomorrow(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    event_id: str,
    user_id: int,
    chat_id: int,
    scope: RecurrenceScope | str | None = None,
    instance_dt: datetime | None = None,
) -> OrchestratorResult:
    event = await calendar_store.get_event(event_id)
    if event is None or event.user_id != user_id or event.chat_id != chat_id:
        return refused("Событие не найдено.", intent="utility_calendar.move", mode="local")
    is_recurring = bool(event.rrule)
    scope_value = normalize_scope(scope)
    if is_recurring and scope_value is None:
        return ok(
            "Это повторяющееся событие. Что изменить?",
            intent="utility_calendar.move",
            mode="local",
            actions=_build_recurrence_scope_actions(
                "calendar.move_tomorrow",
                event_id=event_id,
                instance_dt=instance_dt or event.dt,
            ),
        )
    new_dt = event.dt + timedelta(days=1)
    tool_result = await update_event(
        event_id,
        {"start_at": new_dt},
        scope=scope_value or RecurrenceScope.ALL,
        instance_dt=instance_dt or event.dt,
        user_id=user_id,
        chat_id=chat_id,
        intent="utility_calendar.move",
        request_context=get_request_context(context),
        circuit_breakers=_get_circuit_breakers(context),
        retry_policy=_get_retry_policy(context),
        timeouts=_get_timeouts(context),
    )
    if tool_result.status != "ok":
        return replace(tool_result, mode="local", intent="utility_calendar.move")
    scheduler = _get_reminder_scheduler(context)
    settings = _get_settings(context)
    reminder_id = tool_result.debug.get("reminder_id") if isinstance(tool_result.debug, dict) else None
    if reminder_id and scheduler and settings is not None and settings.reminders_enabled:
        try:
            reminder = await calendar_store.get_reminder(reminder_id)
            if reminder is not None:
                await scheduler.schedule_reminder(reminder)
        except Exception:
            LOGGER.exception("Failed to reschedule reminder: reminder_id=%s", reminder_id)
            return error("Не удалось перенести событие.", intent="utility_calendar.move", mode="local")
    when_label = new_dt.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
    return ok(
        f"Ок, перенёс на {when_label}.",
        intent="utility_calendar.move",
        mode="local",
        debug={
            "refs": {"event_id": event_id},
        },
    )


async def _handle_event_move_phrase(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    event_id: str,
    text: str,
    user_id: int,
    chat_id: int,
    scope: RecurrenceScope | str | None = None,
    instance_dt: datetime | None = None,
) -> OrchestratorResult:
    event = await calendar_store.get_event(event_id)
    if event is None or event.user_id != user_id or event.chat_id != chat_id:
        return refused("Событие не найдено.", intent="utility_calendar.move", mode="local")
    scope_value = normalize_scope(scope)
    if event.rrule and scope_value is None:
        return ok(
            "Это повторяющееся событие. Что изменить?",
            intent="utility_calendar.move",
            mode="local",
            actions=_build_recurrence_scope_actions(
                "calendar.move_phrase",
                event_id=event_id,
                instance_dt=instance_dt or event.dt,
                extra_payload={"text": text},
            ),
        )
    now = datetime.now(tz=calendar_store.BOT_TZ)
    new_dt = parse_datetime_shift(text, base_dt=event.dt, now=now, tz=calendar_store.BOT_TZ)
    if new_dt is None:
        return refused("Не понял, на какое время перенести.", intent="utility_calendar.move", mode="local")
    tool_result = await update_event(
        event_id,
        {"start_at": new_dt},
        scope=scope_value or RecurrenceScope.ALL,
        instance_dt=instance_dt or event.dt,
        user_id=user_id,
        chat_id=chat_id,
        intent="utility_calendar.move",
        request_context=get_request_context(context),
        circuit_breakers=_get_circuit_breakers(context),
        retry_policy=_get_retry_policy(context),
        timeouts=_get_timeouts(context),
    )
    if tool_result.status != "ok":
        return replace(tool_result, mode="local", intent="utility_calendar.move")
    when_label = new_dt.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
    return ok(
        f"Ок, перенёс на {when_label}.",
        intent="utility_calendar.move",
        mode="local",
        debug={"refs": {"event_id": event_id}},
    )


async def _execute_resolution(
    resolution: ResolutionResult,
    *,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
    request_context: RequestContext | None,
    user_context: dict[str, object],
) -> OrchestratorResult:
    if resolution.action == "move_tomorrow" and resolution.target_id:
        return await _handle_event_move_tomorrow(
            context,
            event_id=resolution.target_id,
            user_id=user_id,
            chat_id=chat_id,
            scope=resolution.scope,
        )
    if resolution.action == "move" and resolution.target_id and resolution.text:
        return await _handle_event_move_phrase(
            context,
            event_id=resolution.target_id,
            text=resolution.text,
            user_id=user_id,
            chat_id=chat_id,
            scope=resolution.scope,
        )
    if resolution.action == "cancel" and resolution.target_id:
        if resolution.target == "reminder":
            return await _handle_reminder_delete(
                context,
                reminder_id=resolution.target_id,
                user_id=user_id,
                chat_id=chat_id,
            )
        if resolution.target == "event":
            return await _handle_event_delete(
                context,
                event_id=resolution.target_id,
                scope=resolution.scope,
                user_id=user_id,
            )
    if resolution.action == "repeat_search" and resolution.query:
        orchestrator = _get_orchestrator(context)
        return await orchestrator.handle(
            f"/search {resolution.query}",
            user_context,
            request_context=request_context,
        )
    return _build_resolution_fallback(resolution.action or "resolve", reason=resolution.reason)


def _draft_cancel_action(draft_id: str) -> Action:
    return Action(
        id="calendar.create_cancel",
        label="❌ Отмена",
        payload={"op": "calendar.create_cancel", "draft_id": draft_id},
    )


async def _handle_draft_followup(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    chat_id: int,
    draft_id: str,
    text: str,
) -> OrchestratorResult:
    draft_store = _get_draft_store(context)
    if draft_store is None:
        return refused("Черновик не найден.", intent="calendar.nlp.draft", mode="local")
    draft = draft_store.get_draft(chat_id=chat_id, user_id=user_id, draft_id=draft_id)
    if draft is None:
        draft_store.set_active_draft(chat_id=chat_id, user_id=user_id, draft_id=None)
        return refused("Черновик устарел.", intent="calendar.nlp.draft", mode="local")
    updated = update_draft_from_text(draft, text, now=datetime.now(tz=calendar_store.BOT_TZ), tz=calendar_store.BOT_TZ)
    draft_store.update_draft(chat_id=chat_id, user_id=user_id, draft_id=draft_id, draft=updated)
    if updated.missing_fields:
        draft_store.set_active_draft(chat_id=chat_id, user_id=user_id, draft_id=draft_id)
        return ok(
            _draft_missing_prompt(updated),
            intent="calendar.nlp.clarify",
            mode="local",
            actions=[_draft_cancel_action(draft_id)],
        )
    draft_store.set_active_draft(chat_id=chat_id, user_id=user_id, draft_id=None)
    return ok(
        _render_event_draft(updated),
        intent="calendar.nlp.create",
        mode="local",
        actions=_draft_actions(draft_id),
    )


async def _handle_calendar_draft_confirm(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    chat_id: int,
    draft_id: str,
) -> OrchestratorResult:
    draft_store = _get_draft_store(context)
    if draft_store is None:
        return refused("Черновик не найден.", intent="calendar.nlp.confirm", mode="local")
    draft = draft_store.get_draft(chat_id=chat_id, user_id=user_id, draft_id=draft_id)
    if draft is None:
        return refused("Черновик устарел.", intent="calendar.nlp.confirm", mode="local")
    if draft.missing_fields:
        draft_store.set_active_draft(chat_id=chat_id, user_id=user_id, draft_id=draft_id)
        return ok(
            _draft_missing_prompt(draft),
            intent="calendar.nlp.clarify",
            mode="local",
            actions=[_draft_cancel_action(draft_id)],
        )
    if draft.start_at is None:
        return refused("Нужно указать дату и время.", intent="calendar.nlp.confirm", mode="local")
    settings = _get_settings(context)
    result = await create_event(
        start_at=draft.start_at,
        title=draft.title,
        chat_id=chat_id,
        user_id=user_id,
        intent="utility_calendar.add",
        reminder_scheduler=_get_reminder_scheduler(context),
        reminders_enabled=bool(getattr(settings, "reminders_enabled", True)) if settings else True,
        request_context=get_request_context(context),
        circuit_breakers=_get_circuit_breakers(context),
        retry_policy=_get_retry_policy(context),
        timeouts=_get_timeouts(context),
        recurrence_text=draft.source_text if draft.recurrence else None,
    )
    draft_store.delete_draft(chat_id=chat_id, user_id=user_id, draft_id=draft_id)
    draft_store.set_active_draft(chat_id=chat_id, user_id=user_id, draft_id=None)
    return replace(result, mode="local", intent="utility_calendar.add")


async def _handle_calendar_draft_edit(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    chat_id: int,
    draft_id: str,
) -> OrchestratorResult:
    draft_store = _get_draft_store(context)
    if draft_store is None:
        return refused("Черновик не найден.", intent="calendar.nlp.edit", mode="local")
    draft = draft_store.get_draft(chat_id=chat_id, user_id=user_id, draft_id=draft_id)
    if draft is None:
        return refused("Черновик устарел.", intent="calendar.nlp.edit", mode="local")
    if not draft.missing_fields:
        return ok(
            _render_event_draft(draft),
            intent="calendar.nlp.edit",
            mode="local",
            actions=_draft_actions(draft_id),
        )
    draft_store.set_active_draft(chat_id=chat_id, user_id=user_id, draft_id=draft_id)
    return ok(
        _draft_missing_prompt(draft),
        intent="calendar.nlp.clarify",
        mode="local",
        actions=[_draft_cancel_action(draft_id)],
    )


async def _handle_calendar_draft_cancel(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    chat_id: int,
    draft_id: str,
) -> OrchestratorResult:
    draft_store = _get_draft_store(context)
    if draft_store is None:
        return ok("Ок, отменил.", intent="calendar.nlp.cancel", mode="local")
    draft_store.delete_draft(chat_id=chat_id, user_id=user_id, draft_id=draft_id)
    draft_store.set_active_draft(chat_id=chat_id, user_id=user_id, draft_id=None)
    return ok("Ок, отменил.", intent="calendar.nlp.cancel", mode="local")


async def _handle_reminder_reschedule_start(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    chat_id: int,
    reminder_id: str,
) -> OrchestratorResult:
    if not _wizards_enabled(context):
        return refused(
            "Сценарии отключены.",
            intent="utility_reminders.reschedule",
            mode="local",
        )
    manager = _get_wizard_manager(context)
    if manager is None:
        return error(
            "Сценарий переноса не настроен.",
            intent="utility_reminders.reschedule",
            mode="local",
        )
    return await manager.start_reminder_reschedule(user_id=user_id, chat_id=chat_id, reminder_id=reminder_id)


async def _handle_reminder_add_offset(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    event_id: str,
    minutes: int,
) -> OrchestratorResult:
    event = await calendar_store.get_event(event_id)
    if event is None:
        return refused(
            f"Событие не найдено: {event_id}",
            intent="utility_reminders.add",
            mode="local",
        )
    offset = max(0, minutes)
    trigger_at = event.dt - timedelta(minutes=offset)
    reminder = await calendar_store.ensure_reminder_for_event(event, trigger_at, enabled=True)
    scheduler = _get_reminder_scheduler(context)
    settings = _get_settings(context)
    if scheduler and settings is not None and settings.reminders_enabled:
        try:
            await scheduler.schedule_reminder(reminder)
        except Exception:
            LOGGER.exception("Failed to schedule reminder: reminder_id=%s", reminder.id)
            return error(
                "Не удалось добавить напоминание.",
                intent="utility_reminders.add",
                mode="local",
            )
    when_label = trigger_at.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
    return ok(
        f"Напоминание добавлено на {when_label}.",
        intent="utility_reminders.add",
        mode="local",
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
            intent="utility.calc",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    user_id = update.effective_user.id if update.effective_user else 0
    try:
        result_value = parse_and_eval(expression)
    except CalcError as exc:
        result = error(f"Ошибка вычисления: {exc}", intent="utility.calc", mode="local")
        await send_result(update, context, result)
        return
    result = ok(f"{expression} = {result_value}", intent="utility.calc", mode="local")
    await send_result(update, context, result)


@_with_error_handling
async def calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    args = context.args
    if not args:
        result = refused(
            "Использование: /calendar add YYYY-MM-DD HH:MM <title> (или DD.MM.YYYY HH:MM) | list [YYYY-MM-DD YYYY-MM-DD] | "
            "today | week | del <id> | debug_due.",
            intent="utility.calendar",
            mode="local",
        )
        await send_result(update, context, result)
        return
    command = args[0].lower()
    if command == "add":
        if len(args) < 4:
            result = refused(
                "Использование: /calendar add YYYY-MM-DD HH:MM <title> (или DD.MM.YYYY HH:MM).\n"
                "Быстрее через /menu → Календарь → Добавить: можно написать «завтра 19:00 врач».",
                intent="utility_calendar.add",
                mode="local",
            )
            await send_result(update, context, result)
            return
        date_part = args[1]
        time_part = args[2]
        title_start = 3
        if len(args) >= 5 and args[3] == "-m":
            result = refused(
                "Напоминания создаются в разделе /menu → Напоминания.",
                intent="utility_calendar.add",
                mode="local",
            )
            await send_result(update, context, result)
            return
        title = " ".join(args[title_start:]).strip()
        if not title:
            result = refused(
                "Укажите название события. Пример: /calendar add 2026-02-05 18:30 Врач "
                "(или /calendar add 05.02.2026 18:30 Врач).",
                intent="utility_calendar.add",
                mode="local",
            )
            await send_result(update, context, result)
            return
        try:
            dt = calendar_store.parse_local_datetime(f"{date_part} {time_part}")
        except ValueError:
            result = refused(
                "Неверный формат даты. Пример: /calendar add 2026-02-05 18:30 Врач "
                "(или /calendar add 05.02.2026 18:30 Врач).\n"
                "Быстрее через /menu → Календарь → Добавить: можно написать «завтра 19:00 врач».",
                intent="utility_calendar.add",
                mode="local",
            )
            await send_result(update, context, result)
            return
        chat_id = update.effective_chat.id if update.effective_chat else 0
        request_context = get_request_context(context)
        scheduler = _get_reminder_scheduler(context)
        settings = _get_settings(context)
        reminders_enabled = bool(getattr(settings, "reminders_enabled", False))
        tool_result = await create_event(
            start_at=dt,
            title=title,
            chat_id=chat_id,
            user_id=user_id,
            request_id=request_context.correlation_id if request_context else None,
            intent="utility_calendar.add",
            reminder_scheduler=scheduler,
            reminders_enabled=reminders_enabled,
            request_context=request_context,
            circuit_breakers=_get_circuit_breakers(context),
            retry_policy=_get_retry_policy(context),
            timeouts=_get_timeouts(context),
        )
        result = replace(tool_result, mode="local")
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
        if start is None or end is None:
            start = datetime.now(tz=calendar_store.BOT_TZ)
            end = start + timedelta(days=7)
        result = await _build_calendar_list_result(
            start,
            end,
            user_id=user_id,
            chat_id=chat_id,
            intent="utility_calendar.list",
        )
        await send_result(update, context, result)
        return
    if command == "today":
        today = datetime.now(tz=calendar_store.MOSCOW_TZ).date()
        start, end = calendar_store.day_bounds(today)
        result = await _build_calendar_list_result(
            start,
            end,
            user_id=user_id,
            chat_id=chat_id,
            intent="utility_calendar.list",
        )
        await send_result(update, context, result)
        return
    if command == "week":
        today = datetime.now(tz=calendar_store.MOSCOW_TZ).date()
        start, end = calendar_store.week_bounds(today)
        result = await _build_calendar_list_result(
            start,
            end,
            user_id=user_id,
            chat_id=chat_id,
            intent="utility_calendar.list",
        )
        await send_result(update, context, result)
        return
    if command == "del":
        if len(args) < 2:
            result = refused(
                "Использование: /calendar del <id>.",
                intent="utility_calendar.delete",
                mode="local",
            )
            await send_result(update, context, result)
            return
        item_id = args[1].strip()
        if not item_id:
            result = refused("Укажите id для удаления.", intent="utility_calendar.delete", mode="local")
            await send_result(update, context, result)
            return
        scope = parse_recurrence_scope(" ".join(args[2:])) if len(args) > 2 else None
        event = await calendar_store.get_event(item_id)
        if event is not None and event.rrule and scope is None:
            result = ok(
                "Это повторяющееся событие. Что изменить?",
                intent="utility_calendar.delete",
                mode="local",
                actions=_build_recurrence_scope_actions(
                    "calendar.delete",
                    event_id=item_id,
                    instance_dt=event.dt,
                ),
            )
            await send_result(update, context, result)
            return
        tool_result = await delete_event(
            item_id,
            scope=scope or RecurrenceScope.ALL,
            instance_dt=event.dt if event else None,
            intent="utility_calendar.delete",
            user_id=user_id,
            request_context=request_context,
            circuit_breakers=_get_circuit_breakers(context),
            retry_policy=_get_retry_policy(context),
            timeouts=_get_timeouts(context),
        )
        reminder_id = tool_result.debug.get("reminder_id") if isinstance(tool_result.debug, dict) else None
        scheduler = _get_reminder_scheduler(context)
        if reminder_id and scheduler:
            try:
                await scheduler.cancel_reminder(reminder_id)
            except Exception:
                LOGGER.exception("Failed to cancel reminder: reminder_id=%s", reminder_id)
        result = replace(tool_result, mode="local")
        await send_result(update, context, result)
        return
    if command == "debug_due":
        now = datetime.now(tz=calendar_store.BOT_TZ)
        due_items = await calendar_store.list_due_reminders(now, limit=5)
        if not due_items:
            result = ok("Нет просроченных напоминаний.", intent="utility_calendar.debug_due", mode="local")
            await send_result(update, context, result)
            return
        lines = []
        for item in due_items:
            remind_label = item.trigger_at.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
            lines.append(
                f"{item.id} | trigger_at={remind_label} | enabled={item.enabled} | {item.text}"
            )
        result = ok("\n".join(lines), intent="utility_calendar.debug_due", mode="local")
        await send_result(update, context, result)
        return
    result = refused(
        "Неизвестная команда. Использование: /calendar add|list|today|week|del|debug_due.",
        intent="utility.calendar",
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
    limit = 10
    if context.args:
        try:
            limit = max(1, int(context.args[0]))
        except ValueError:
            result = refused("Использование: /reminders [N].", intent="utility_reminders.list", mode="local")
            await send_result(update, context, result)
            return
    result = await _handle_reminders_list(
        context,
        user_id=user_id,
        chat_id=update.effective_chat.id if update.effective_chat else 0,
        limit=limit,
        intent="utility_reminders.list",
    )
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
async def document_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context):
        return
    message = update.message
    if message is None:
        return
    settings = _get_settings(context)
    if settings is None:
        await send_result(update, context, error("Настройки не загружены.", intent="document.upload", mode="local"))
        return
    document_store = _get_document_store(context)
    if document_store is None:
        await send_result(
            update,
            context,
            error("Хранилище документов недоступно.", intent="document.upload", mode="local"),
        )
        return
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    file_id = ""
    file_type = ""
    extension = ""
    if message.document is not None:
        detected = _detect_document_type(message.document)
        if detected is None:
            await send_result(
                update,
                context,
                refused(
                    "Поддерживаются PDF, DOCX и изображения с текстом.",
                    intent="document.upload",
                    mode="local",
                ),
            )
            return
        file_type, extension = detected
        file_id = message.document.file_id
    elif message.photo:
        photo = message.photo[-1]
        file_id = photo.file_id
        file_type = "image"
        extension = ".jpg"
    else:
        return
    user_dir = settings.uploads_path / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    file_path = user_dir / f"{file_id}{extension}"
    file_obj = await context.bot.get_file(file_id)
    await file_obj.download_to_drive(custom_path=str(file_path))
    extractor = FileTextExtractor(ocr_enabled=settings.ocr_enabled)
    try:
        extracted = extractor.extract(path=file_path, file_type=file_type)
    except OCRNotAvailableError:
        await send_result(
            update,
            context,
            error(
                "OCR недоступен. Установите tesseract или отключите OCR.",
                intent="document.ocr_missing",
                mode="local",
            ),
        )
        return
    except Exception:
        await send_result(
            update,
            context,
            error("Не удалось извлечь текст из документа.", intent="document.extract", mode="local"),
        )
        return
    if not extracted.text.strip():
        await send_result(
            update,
            context,
            refused("Не удалось извлечь текст.", intent="document.extract.empty", mode="local"),
        )
        return
    text_dir = settings.document_texts_path / str(user_id)
    text_dir.mkdir(parents=True, exist_ok=True)
    text_path = text_dir / f"{file_id}.txt"
    text_path.write_text(extracted.text, encoding="utf-8")
    session = document_store.create_session(
        user_id=user_id,
        chat_id=chat_id,
        file_path=str(file_path),
        file_type=file_type,
        text_path=str(text_path),
    )
    result = ok(
        "Документ обработан. Что сделать?",
        intent="document.processed",
        mode="local",
        actions=_document_actions(session.doc_id),
    )
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
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    if menu.is_menu_label(prompt):
        result = refused(
            "Используй /menu и нажимай кнопки, или введи команду /calc ...",
            intent="guard.menu_label",
            mode="local",
        )
        await send_result(update, context, result)
        return
    document_store = _get_document_store(context)
    if document_store is not None:
        active_session = document_store.get_active(user_id=user_id, chat_id=chat_id)
        if active_session and active_session.state == "qa_mode":
            result = await _handle_document_question(
                context,
                user_id=user_id,
                chat_id=chat_id,
                question=prompt,
            )
            await send_result(update, context, result)
            return
    if _wizards_enabled(context):
        manager = _get_wizard_manager(context)
        if manager is not None:
            wizard_result = await manager.handle_text(user_id=user_id, chat_id=chat_id, text=prompt)
            if wizard_result is not None:
                await send_result(update, context, wizard_result)
                return
    draft_store = _get_draft_store(context)
    if draft_store is not None:
        active_draft_id = draft_store.get_active_draft_id(chat_id=chat_id, user_id=user_id)
        if active_draft_id:
            result = await _handle_draft_followup(
                context,
                user_id=user_id,
                chat_id=chat_id,
                draft_id=active_draft_id,
                text=prompt,
            )
            await send_result(update, context, result)
            return
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
    request_id = request_context.correlation_id if request_context else None
    memory_context = _build_memory_context(context)
    user_context = _build_user_context_with_dialog(
        update,
        dialog_context=dialog_context,
        dialog_message_count=dialog_count,
        memory_context=memory_context,
        request_id=request_id,
        request_context=request_context,
    )
    last_state_store = _get_last_state_store(context)
    last_state = (
        last_state_store.get_state(chat_id=chat_id, user_id=user_id) if last_state_store else None
    )
    resolution = resolve_short_message(prompt, last_state)
    if resolution.status != "skip":
        _log_memory_resolution(
            request_context,
            used=resolution.status == "matched",
            reason=resolution.reason,
            matched_ref=resolution.matched_ref,
        )
        if resolution.status == "matched":
            result = await _execute_resolution(
                resolution,
                context=context,
                user_id=user_id,
                chat_id=chat_id,
                request_context=request_context,
                user_context=user_context,
            )
        else:
            result = _build_resolution_fallback(resolution.action or "resolve", reason=resolution.reason)
        await send_result(update, context, result)
        if dialog_memory and await dialog_memory.is_enabled(user_id) and _should_store_assistant_response(result):
            await dialog_memory.add_assistant(user_id, chat_id, result.text)
        return
    if draft_store is not None:
        force_nlp = draft_store.consume_force_nlp(chat_id=chat_id, user_id=user_id)
        if force_nlp or is_calendar_intent(prompt):
            now = datetime.now(tz=calendar_store.BOT_TZ)
            draft = event_from_text_ru(prompt, now=now, tz=calendar_store.BOT_TZ, last_state=last_state)
            draft_id = generate_draft_id()
            draft_store.save_draft(chat_id=chat_id, user_id=user_id, draft_id=draft_id, draft=draft)
            result = ok(
                _render_event_draft(draft),
                intent="calendar.nlp.create",
                mode="local",
                actions=_draft_actions(draft_id),
            )
            await send_result(update, context, result)
            if dialog_memory and await dialog_memory.is_enabled(user_id) and _should_store_assistant_response(result):
                await dialog_memory.add_assistant(user_id, chat_id, result.text)
            return
    try:
        result = await orchestrator.handle(
            prompt,
            user_context,
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
    message = _build_selfcheck_message(context)
    result = _build_simple_result(message, intent="command.selfcheck", status="ok", mode="local")
    await send_result(update, context, result)


def _build_selfcheck_message(context: ContextTypes.DEFAULT_TYPE) -> str:
    settings = context.application.bot_data["settings"]
    allowlist_snapshot = _get_allowlist_store(context).snapshot()
    allowed_user_ids = allowlist_snapshot.allowed_user_ids
    if allowed_user_ids:
        allowed_summary = f"ok ({len(allowed_user_ids)}): {', '.join(map(str, allowed_user_ids))}"
    else:
        allowed_summary = "empty (доступ закрыт)"
    integrations = _active_integrations(context)
    integrations_label = ", ".join(sorted(integrations.keys())) if integrations else "none"
    return (
        "Self-check:\n"
        f"ALLOWLIST_PATH: {settings.allowlist_path}\n"
        f"ALLOWLIST_USERS: {allowed_summary}\n"
        f"RATE_LIMIT_PER_MINUTE: {settings.rate_limit_per_minute}\n"
        f"RATE_LIMIT_PER_DAY: {settings.rate_limit_per_day}\n"
        f"HISTORY_SIZE: {settings.history_size}\n"
        f"TELEGRAM_MESSAGE_LIMIT: {settings.telegram_message_limit}\n"
        f"INTEGRATIONS: {integrations_label}"
    )


def _active_integrations(context: ContextTypes.DEFAULT_TYPE) -> dict[str, bool]:
    settings = context.application.bot_data["settings"]
    llm_client = context.application.bot_data.get("llm_client")
    integrations: dict[str, bool] = {}
    if settings.calendar_backend == "caldav" and tools_calendar.is_caldav_configured(settings):
        integrations["caldav"] = True
    if llm_client is not None:
        integrations["llm"] = True
    return integrations


@_with_error_handling
async def health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context, bucket="ui"):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    request_context = get_request_context(context)
    actions: list[Action] = []
    if request_context is not None and request_context.env == "dev":
        actions.append(Action(id="debug.trace_last", label="Trace last", payload={"op": "trace_last"}))
        actions.append(
            Action(
                id="debug.show_config",
                label="Show config",
                payload={"op": "run_command", "command": "/config", "args": ""},
            )
        )
    result = ok(
        await _build_health_message(context, user_id=user_id),
        intent="command.health",
        mode="local",
        actions=actions,
    )
    await send_result(update, context, result)


@_with_error_handling
async def config_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_access(update, context, bucket="ui"):
        return
    request_context = get_request_context(context)
    if request_context is None or request_context.env != "dev":
        result = _build_simple_result(
            "Команда недоступна в prod.",
            intent="command.config",
            status="refused",
            mode="local",
        )
        await send_result(update, context, result)
        return
    result = _build_simple_result(
        _build_config_message(context),
        intent="command.config",
        status="ok",
        mode="local",
    )
    await send_result(update, context, result)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_status(context, "error")
    error = context.error
    if isinstance(error, telegram.error.NetworkError):
        message = str(error)
        if update is None or "get_updates" in message or "getUpdates" in message:
            LOGGER.info("NetworkError during polling shutdown: %s", message)
            return
    LOGGER.exception("Unhandled exception", exc_info=error)
    if isinstance(update, Update) and update.message:
        result = _build_simple_result(
            "Ошибка на сервере. Попробуй ещё раз.",
            intent="error.general",
            status="error",
            mode="local",
        )
        await send_result(update, context, result)
