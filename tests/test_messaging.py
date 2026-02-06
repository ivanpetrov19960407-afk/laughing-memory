from __future__ import annotations

import asyncio
from types import SimpleNamespace

from telegram.error import BadRequest

from app.infra.messaging import safe_edit_text


class DummyMessage:
    def __init__(self) -> None:
        self.reply_calls: list[str] = []

    async def reply_text(self, text, reply_markup=None):
        self.reply_calls.append(text)


class DummyCallbackQuery:
    def __init__(self) -> None:
        self.answer_called = False
        self.answer_text = None

    async def edit_message_text(self, *args, **kwargs):
        raise BadRequest("Message is not modified")

    async def answer(self, text=None):
        self.answer_called = True
        self.answer_text = text


def test_safe_edit_text_message_not_modified_calls_answer() -> None:
    message = DummyMessage()
    callback_query = DummyCallbackQuery()
    update = SimpleNamespace(effective_message=message, callback_query=callback_query)

    asyncio.run(safe_edit_text(update, None, "Проверка"))

    assert callback_query.answer_called
