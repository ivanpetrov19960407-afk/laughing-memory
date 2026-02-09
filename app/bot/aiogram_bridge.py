"""Bridge to run PTB handlers from aiogram 3: fake Update/Context and bot adapter.

Converts PTB InlineKeyboardMarkup to aiogram when sending; provides
effective_message.reply_text and callback_query.edit_message_text/answer
so that app.infra.messaging (safe_send_text, safe_edit_text) works unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

LOGGER = logging.getLogger(__name__)


def _ptb_markup_to_aiogram(ptb_markup: InlineKeyboardMarkup | None):  # noqa: ANN201
    if ptb_markup is None:
        return None
    from aiogram.types import InlineKeyboardButton as AioButton
    from aiogram.types import InlineKeyboardMarkup as AioMarkup

    rows = []
    for row in ptb_markup.inline_keyboard:
        buttons = []
        for btn in row:
            if getattr(btn, "callback_data", None):
                buttons.append(AioButton(text=btn.text, callback_data=btn.callback_data))
            elif getattr(btn, "url", None):
                buttons.append(AioButton(text=btn.text, url=btn.url))
            else:
                buttons.append(AioButton(text=btn.text, callback_data=""))
        rows.append(buttons)
    return AioMarkup(inline_keyboard=rows)


def _convert_markup(markup: Any):  # noqa: ANN401
    if markup is None:
        return None
    if isinstance(markup, InlineKeyboardMarkup):
        return _ptb_markup_to_aiogram(markup)
    return markup


def make_fake_update_and_context_for_message(
    *,
    user_id: int,
    chat_id: int,
    message_id: int,
    text: str,
    caption: str,
    aiogram_bot,
    bot_data: dict[str, Any],
) -> tuple[Any, Any]:
    """Build PTB-like update and context from aiogram Message (or equivalent)."""
    from aiogram.types import Message as AioMessage

    class FakeMessage:
        def __init__(self) -> None:
            self.message_id = message_id
            self.text = text or ""
            self.caption = caption or ""
            self.chat = type("Chat", (), {"id": chat_id})()

        async def reply_text(self, t: str, reply_markup=None) -> None:
            await aiogram_bot.send_message(
                chat_id=chat_id,
                text=t or "(пустой ответ)",
                reply_markup=_convert_markup(reply_markup),
            )

    class FakeUser:
        id = user_id

    class FakeChat:
        id = chat_id

    effective_message = FakeMessage()
    update = type("Update", (), {
        "effective_user": FakeUser(),
        "effective_chat": FakeChat(),
        "effective_message": effective_message,
        "callback_query": None,
    })()
    application = type("Application", (), {"bot_data": bot_data})()
    chat_data: dict[str, Any] = {}
    context = type("Context", (), {
        "bot": _AiogramBotAdapter(aiogram_bot),
        "application": application,
        "chat_data": chat_data,
    })()
    return update, context


def make_fake_update_and_context_for_callback(
    *,
    user_id: int,
    chat_id: int,
    message_id: int,
    message_text: str,
    callback_data: str,
    aiogram_bot,
    bot_data: dict[str, Any],
) -> tuple[Any, Any]:
    """Build PTB-like update and context from aiogram CallbackQuery."""
    from aiogram.types import CallbackQuery as AioCallbackQuery

    class FakeMessage:
        def __init__(self) -> None:
            self.message_id = message_id
            self.text = message_text or ""
            self.caption = ""
            self.chat = type("Chat", (), {"id": chat_id})()

        async def reply_text(self, t: str, reply_markup=None) -> None:
            await aiogram_bot.send_message(
                chat_id=chat_id,
                text=t or "(пустой ответ)",
                reply_markup=_convert_markup(reply_markup),
            )

    class FakeCallbackMessage:
        message_id = message_id
        chat = type("Chat", (), {"id": chat_id})()

    class FakeCallbackQuery:
        data = callback_data
        message = FakeCallbackMessage()

        async def edit_message_text(self, text: str, reply_markup=None) -> None:
            await aiogram_bot.edit_message_text(
                text=text or "(пустой ответ)",
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=_convert_markup(reply_markup),
            )

        async def answer(self, text: str | None = None) -> None:
            from aiogram.types import CallbackQuery as AioCQ
            # Answer is done by aiogram dispatcher after handler; we no-op or call bot.answer_callback_query
            pass

    effective_message = FakeMessage()
    update = type("Update", (), {
        "effective_user": type("User", (), {"id": user_id})(),
        "effective_chat": type("Chat", (), {"id": chat_id})(),
        "effective_message": effective_message,
        "callback_query": FakeCallbackQuery(),
    })()
    application = type("Application", (), {"bot_data": bot_data})()
    chat_data = {}
    context = type("Context", (), {
        "bot": _AiogramBotAdapter(aiogram_bot),
        "application": application,
        "chat_data": chat_data,
    })()
    return update, context


class _AiogramBotAdapter:
    """Mimics PTB bot for send_message / edit_message_text with markup conversion."""

    def __init__(self, aiogram_bot: Any) -> None:
        self._bot = aiogram_bot

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Any = None,
        **kwargs: Any,
    ) -> Any:
        return await self._bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=_convert_markup(reply_markup),
            **kwargs,
        )

    async def edit_message_text(
        self,
        text: str,
        chat_id: int,
        message_id: int,
        reply_markup: Any = None,
        **kwargs: Any,
    ) -> Any:
        return await self._bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=_convert_markup(reply_markup),
            **kwargs,
        )
