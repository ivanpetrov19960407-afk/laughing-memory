"""Smoke tests for aiogram 3 bridge: fake update/context and handler mapping."""

from __future__ import annotations

from types import SimpleNamespace

from app.bot.aiogram_bridge import (
    _ptb_markup_to_aiogram,
    make_fake_update_and_context_for_callback,
    make_fake_update_and_context_for_message,
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def test_make_fake_update_for_message() -> None:
    bot = SimpleNamespace(
        send_message=lambda *a, **k: None,
        edit_message_text=lambda *a, **k: None,
    )
    bot_data = {"orchestrator": None}
    update, context = make_fake_update_and_context_for_message(
        user_id=1,
        chat_id=2,
        message_id=3,
        text="/start",
        caption="",
        aiogram_bot=bot,
        bot_data=bot_data,
    )
    assert update.effective_user.id == 1
    assert update.effective_chat.id == 2
    assert update.effective_message.message_id == 3
    assert update.effective_message.text == "/start"
    assert update.callback_query is None
    assert context.application.bot_data is bot_data
    assert context.bot is not None


def test_make_fake_update_for_callback() -> None:
    bot = SimpleNamespace(
        send_message=lambda *a, **k: None,
        edit_message_text=lambda *a, **k: None,
    )
    bot_data = {}
    update, context = make_fake_update_and_context_for_callback(
        user_id=1,
        chat_id=2,
        message_id=3,
        message_text="Menu",
        callback_data="cb:menu:open",
        aiogram_bot=bot,
        bot_data=bot_data,
    )
    assert update.effective_user.id == 1
    assert update.callback_query is not None
    assert update.callback_query.data == "cb:menu:open"
    assert context.chat_data is not None


def test_ptb_markup_to_aiogram() -> None:
    ptb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton("OK", callback_data="a:token123")],
        ]
    )
    aio = _ptb_markup_to_aiogram(ptb)
    assert aio is not None
    assert len(aio.inline_keyboard) == 1
    assert len(aio.inline_keyboard[0]) == 1
    assert aio.inline_keyboard[0][0].text == "OK"
    assert aio.inline_keyboard[0][0].callback_data == "a:token123"


def test_ptb_markup_to_aiogram_none() -> None:
    assert _ptb_markup_to_aiogram(None) is None
