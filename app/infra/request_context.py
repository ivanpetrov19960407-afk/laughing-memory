from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from telegram import Update
from telegram.ext import ContextTypes

LOGGER = logging.getLogger(__name__)

_CONTEXT_KEY = "_request_context"


@dataclass
class RequestContext:
    request_id: str
    user_id: int
    username: str
    chat_id: int
    text: str
    start_time: float
    status: str = "ok"
    response_size: int = 0


def _truncate_text(text: str, limit: int = 200) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _extract_text(update: Update | None) -> str:
    if update is None:
        return ""
    message = update.effective_message
    if not message:
        return ""
    return message.text or message.caption or ""


def start_request(update: Update | None, context: ContextTypes.DEFAULT_TYPE | None) -> RequestContext:
    request_id = uuid.uuid4().hex[:10]
    user = update.effective_user if update else None
    chat = update.effective_chat if update else None
    request_context = RequestContext(
        request_id=request_id,
        user_id=user.id if user else 0,
        username=user.username if user and user.username else "",
        chat_id=chat.id if chat else 0,
        text=_extract_text(update),
        start_time=time.monotonic(),
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


def add_response_size(context: ContextTypes.DEFAULT_TYPE | None, size: int) -> None:
    request_context = get_request_context(context)
    if request_context:
        request_context.response_size += max(size, 0)


def log_request(logger: logging.Logger, request_context: RequestContext) -> None:
    duration_ms = (time.monotonic() - request_context.start_time) * 1000
    truncated_text = _truncate_text(request_context.text)
    message = (
        "request_id={request_id} user_id={user_id} username={username} chat_id={chat_id} "
        "status={status} duration_ms={duration_ms:.1f} response_size={response_size} text=\"{text}\""
    ).format(
        request_id=request_context.request_id,
        user_id=request_context.user_id,
        username=request_context.username or "-",
        chat_id=request_context.chat_id,
        status=request_context.status,
        duration_ms=duration_ms,
        response_size=request_context.response_size,
        text=truncated_text,
    )
    if request_context.status == "ratelimited":
        logger.warning(message)
    elif request_context.status == "error":
        logger.error(message)
    else:
        logger.info(message)
