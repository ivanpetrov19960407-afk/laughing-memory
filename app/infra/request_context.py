from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

LOGGER = logging.getLogger(__name__)

_CONTEXT_KEY = "_request_context"
_DEV_ENVS = {"dev", "development", "local"}
_SECRET_KEYS = {"authorization", "api_key", "apikey", "token", "password", "secret", "headers", "cookie"}
_TEXT_KEYS = {"text", "prompt", "input_text", "message", "payload", "response", "content"}
_RAW_TEXT_KEYS = {"where", "exc_type", "mode", "model", "provider", "tool_name", "intent", "handler"}


@dataclass
class RequestContext:
    correlation_id: str
    user_id: str | int
    chat_id: str | int
    message_id: str | int
    timezone: str | None
    ts: datetime
    env: str
    meta: dict[str, Any] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    input_text: str = ""
    start_time: float = field(default_factory=time.monotonic)
    status: str = "ok"
    response_size: int = 0


def _truncate_text(text: str, limit: int = 120) -> str:
    cleaned = text.replace("\n", " ").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "…"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _env_label() -> str:
    env = os.getenv("APP_ENV", "prod").strip().lower()
    return "dev" if env in _DEV_ENVS else "prod"


def _extract_message_id(update: Update | None) -> int:
    if update is None:
        return 0
    if update.callback_query and getattr(update.callback_query, "message", None):
        message_id = getattr(update.callback_query.message, "message_id", None)
        if isinstance(message_id, int):
            return message_id
    message = update.effective_message
    if not message:
        return 0
    message_id = getattr(message, "message_id", None)
    if isinstance(message_id, int):
        return message_id
    return 0


def _extract_input_text(update: Update | None) -> str:
    if update is None:
        return ""
    if update.callback_query:
        return update.callback_query.data or ""
    message = update.effective_message
    if not message:
        return ""
    return message.text or message.caption or ""


def start_request(update: Update | None, context: ContextTypes.DEFAULT_TYPE | None) -> RequestContext:
    correlation_id = str(uuid.uuid4())
    user = update.effective_user if update else None
    chat = update.effective_chat if update else None
    request_context = RequestContext(
        correlation_id=correlation_id,
        user_id=user.id if user else 0,
        chat_id=chat.id if chat else 0,
        message_id=_extract_message_id(update),
        timezone=None,
        ts=datetime.now(timezone.utc),
        env=_env_label(),
        input_text=_extract_input_text(update),
        meta={},
    )
    if context is not None:
        context.chat_data[_CONTEXT_KEY] = request_context
    return request_context


def get_request_context(context: ContextTypes.DEFAULT_TYPE | None) -> RequestContext | None:
    if context is None:
        return None
    return context.chat_data.get(_CONTEXT_KEY)


def set_status(context: ContextTypes.DEFAULT_TYPE | None, status: str) -> None:
    request_context = get_request_context(context)
    if request_context:
        request_context.status = status


def set_input_text(context: ContextTypes.DEFAULT_TYPE | None, text: str) -> None:
    request_context = get_request_context(context)
    if request_context:
        request_context.input_text = text


def add_response_size(context: ContextTypes.DEFAULT_TYPE | None, size: int) -> None:
    request_context = get_request_context(context)
    if request_context:
        request_context.response_size += max(size, 0)


def add_trace(
    request_context: RequestContext | None,
    *,
    step: str,
    component: str,
    name: str | None = None,
    status: str = "ok",
    duration_ms: float | None = None,
) -> None:
    if request_context is None:
        return
    request_context.trace.append(
        {
            "step": step,
            "component": component,
            "name": name,
            "status": status,
            "duration_ms": duration_ms,
        }
    )


def build_args_shape(data: Any) -> Any:
    if isinstance(data, dict):
        return {str(key): build_args_shape(value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [build_args_shape(item) for item in data]
    return type(data).__name__


def _sanitize_error_message(request_context: RequestContext | None, message: str) -> str:
    env = request_context.env if request_context else "prod"
    if env == "dev":
        return message
    return _truncate_text(message, limit=120)


def elapsed_ms(start_time: float) -> float:
    return max((time.monotonic() - start_time) * 1000, 0.01)


def safe_log_payload(request_context: RequestContext | None, data: Any) -> Any:
    env = request_context.env if request_context else "prod"

    def _safe_text(text: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "text_len": len(text),
            "text_sha256": _hash_text(text),
        }
        if env == "dev":
            payload["text_preview"] = _truncate_text(text)
        return payload

    if isinstance(data, str):
        return _safe_text(data)
    if isinstance(data, bytes):
        return {"bytes_len": len(data)}
    if isinstance(data, dict):
        sanitized: dict[str, Any] = {}
        for key, value in data.items():
            key_lower = str(key).lower()
            if key_lower in _SECRET_KEYS:
                sanitized[key] = "***"
                continue
            if key_lower in _RAW_TEXT_KEYS and isinstance(value, str):
                sanitized[key] = value
                continue
            if key_lower in _TEXT_KEYS:
                sanitized[key] = _safe_text(str(value))
                continue
            sanitized[key] = safe_log_payload(request_context, value)
        return sanitized
    if isinstance(data, list):
        return [safe_log_payload(request_context, item) for item in data]
    if isinstance(data, tuple):
        return [safe_log_payload(request_context, item) for item in data]
    return data


def log_event(
    logger: logging.Logger,
    request_context: RequestContext | None,
    *,
    component: str,
    event: str,
    status: str = "ok",
    duration_ms: float | None = None,
    **fields: Any,
) -> None:
    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": request_context.correlation_id if request_context else "-",
        "component": component,
        "event": event,
        "status": status,
        "env": request_context.env if request_context else "prod",
    }
    if duration_ms is not None:
        payload["duration_ms"] = round(duration_ms, 2)
    if fields:
        payload.update(safe_log_payload(request_context, fields))
    message = json.dumps(payload, ensure_ascii=False)
    if status == "error":
        logger.error(message)
    elif status == "refused":
        logger.warning(message)
    else:
        logger.info(message)


def log_error(
    logger: logging.Logger,
    request_context: RequestContext | None,
    *,
    component: str,
    where: str,
    exc: Exception,
    extra: dict[str, Any] | None = None,
) -> None:
    env = request_context.env if request_context else "prod"
    exc_msg = _sanitize_error_message(request_context, str(exc))
    payload: dict[str, Any] = {
        "where": where,
        "exc_type": type(exc).__name__,
        "exc_msg": exc_msg,
        "user_id": request_context.user_id if request_context else 0,
        "chat_id": request_context.chat_id if request_context else 0,
    }
    if env == "dev":
        payload["stack"] = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if request_context is not None:
        request_context.meta.setdefault(
            "error",
            {
                "where": where,
                "exc_type": type(exc).__name__,
            },
        )
    if extra:
        payload.update(extra)
    log_event(
        logger,
        request_context,
        component=component,
        event="error",
        status="error",
        **payload,
    )


def log_request(logger: logging.Logger, request_context: RequestContext) -> None:
    duration_ms = elapsed_ms(request_context.start_time)
    log_event(
        logger,
        request_context,
        component="handler",
        event="trace.summary",
        status=request_context.status,
        duration_ms=duration_ms,
        response_size=request_context.response_size,
        trace=request_context.trace,
    )
