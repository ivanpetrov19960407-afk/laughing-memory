"""Entry point for running the Secretary bot on aiogram 3.x.

Uses the same Orchestrator and handlers as the PTB entry (app.main); builds
fake PTB Update/Context from aiogram events so handlers and ActionStore stay unchanged.
Run: python bot_aiogram.py
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot import handlers
from app.bot.aiogram_bridge import (
    make_fake_update_and_context_for_callback,
    make_fake_update_and_context_for_message,
)
from app.infra.logging_config import configure_logging, log_exception
from app.infra.request_context import start_request

# Build PTB application (and bot_data) without running PTB polling
from app.main import build_ptb_application

LOGGER = logging.getLogger(__name__)


def _command_list():
    return [
        "start", "help", "ping", "tasks", "task", "last", "ask", "summary", "search",
        "trace", "facts_on", "facts_off", "context_on", "context_off", "context_clear",
        "context_status", "memory_status", "memory_clear", "memory", "profile", "profile_set",
        "remember", "forget", "history", "history_find", "allow", "deny", "allowlist",
        "menu", "cancel", "image", "check", "rewrite", "explain", "calc", "calendar",
        "caldav", "reminders", "reminder_off", "reminder_on", "selfcheck", "health", "config",
    ]


def _make_command_handler(cmd: str):
    handler = getattr(handlers, cmd, None) or getattr(handlers, f"{cmd}_command", None)
    if handler is None:
        if cmd == "start":
            handler = handlers.start
        elif cmd == "help":
            handler = handlers.help_command
        elif cmd == "trace":
            handler = handlers.trace_command
        elif cmd == "facts_on":
            handler = handlers.facts_on
        elif cmd == "facts_off":
            handler = handlers.facts_off
        elif cmd == "memory":
            handler = handlers.memory_command
        elif cmd == "profile":
            handler = handlers.profile_command
        elif cmd == "profile_set":
            handler = handlers.profile_set_command
        elif cmd == "remember":
            handler = handlers.remember_command
        elif cmd == "forget":
            handler = handlers.forget_command
        elif cmd == "history":
            handler = handlers.history_command
        elif cmd == "history_find":
            handler = handlers.history_search_command
        elif cmd == "caldav":
            handler = handlers.caldav_settings
        elif cmd == "config":
            handler = handlers.config_command
        else:
            handler = getattr(handlers, cmd, None)
    return handler


async def _on_message(message: Message, bot: Bot, bot_data: dict) -> None:
    text = (message.text or message.caption or "").strip()
    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id if message.chat else 0
    message_id = message.message_id
    caption = message.caption or ""
    update, context = make_fake_update_and_context_for_message(
        user_id=user_id,
        chat_id=chat_id,
        message_id=message_id,
        text=message.text or "",
        caption=caption,
        aiogram_bot=bot,
        bot_data=bot_data,
    )
    start_request(update, context)
    if message.text and message.text.startswith("/"):
        cmd = message.text.split()[0].lstrip("/").split("@")[0]
        if cmd in _command_list():
            handler = _make_command_handler(cmd)
            if handler:
                try:
                    await handler(update, context)
                except Exception as e:
                    LOGGER.exception("Command %s failed: %s", cmd, e)
                    await handlers.error_handler(update, context)
            return
        await handlers.unknown_command(update, context)
        return
    await handlers.chat(update, context)


async def _on_callback(callback: CallbackQuery, bot: Bot, bot_data: dict) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    chat_id = callback.message.chat.id if callback.message and callback.message.chat else 0
    message_id = callback.message.message_id if callback.message else 0
    message_text = callback.message.text or "" if callback.message else ""
    data = callback.data or ""
    update, context = make_fake_update_and_context_for_callback(
        user_id=user_id,
        chat_id=chat_id,
        message_id=message_id,
        message_text=message_text,
        callback_data=data,
        aiogram_bot=bot,
        bot_data=bot_data,
    )
    start_request(update, context)
    try:
        if data.startswith("cb:"):
            await handlers.static_callback(update, context)
        else:
            await handlers.action_callback(update, context)
    except Exception as e:
        LOGGER.exception("Callback failed: %s", e)
        await handlers.error_handler(update, context)
    try:
        await callback.answer()
    except Exception:
        pass


async def _on_document_or_photo(message: Message, bot: Bot, bot_data: dict) -> None:
    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id if message.chat else 0
    message_id = message.message_id
    update, context = make_fake_update_and_context_for_message(
        user_id=user_id,
        chat_id=chat_id,
        message_id=message_id,
        text=message.caption or "",
        caption=message.caption or "",
        aiogram_bot=bot,
        bot_data=bot_data,
    )
    start_request(update, context)
    try:
        await handlers.document_upload(update, context)
    except Exception as e:
        LOGGER.exception("Document upload failed: %s", e)
        await handlers.error_handler(update, context)


async def _on_startup(dispatcher: Dispatcher) -> None:
    app = dispatcher.get("ptb_application")
    if app and getattr(app, "post_init", None):
        await app.post_init(app)


def main() -> None:
    configure_logging()
    try:
        application, settings = build_ptb_application()
    except RuntimeError as exc:
        log_exception(LOGGER, "Startup failed: %s", exc)
        raise SystemExit(str(exc)) from exc

    bot_data = application.bot_data
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp["ptb_application"] = application
    dp["bot_data"] = bot_data

    dp.startup.register(_on_startup)

    def _make_cmd_wrapper(ptb_handler: Callable[..., Awaitable[None]], data: dict):
        async def _wrapper(message: Message, bot: Bot) -> None:
            update, context = make_fake_update_and_context_for_message(
                user_id=message.from_user.id if message.from_user else 0,
                chat_id=message.chat.id,
                message_id=message.message_id,
                text=message.text or "",
                caption=message.caption or "",
                aiogram_bot=bot,
                bot_data=data,
            )
            start_request(update, context)
            try:
                await ptb_handler(update, context)
            except Exception as e:
                LOGGER.exception("Handler failed: %s", e)
                await handlers.error_handler(update, context)

        return _wrapper

    for cmd in _command_list():
        handler = _make_command_handler(cmd)
        if handler is None:
            continue
        dp.message.register(_make_cmd_wrapper(handler, bot_data), Command(cmd))

    dp.message.register(
        lambda m, b: _on_document_or_photo(m, b, bot_data),
        F.document | F.photo,
    )
    dp.message.register(
        lambda m, b: _on_message(m, b, bot_data),
        F.text,
    )
    dp.callback_query.register(
        lambda c, b: _on_callback(c, b, bot_data),
    )

    LOGGER.info("Aiogram bot started")
    asyncio.run(dp.start_polling(bot))


if __name__ == "__main__":
    main()
