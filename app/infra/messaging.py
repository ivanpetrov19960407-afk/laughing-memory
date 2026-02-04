from __future__ import annotations

import logging
from typing import Iterable

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from app.infra.request_context import add_response_size

LOGGER = logging.getLogger(__name__)

MAX_CHUNK_SIZE = 3500
FALLBACK_CHUNK_SIZE = 2000
EMPTY_MESSAGE_PLACEHOLDER = "(пустой ответ)"


def chunk_text(text: str, max_len: int = MAX_CHUNK_SIZE) -> list[str]:
    chunks: list[str] = []
    remaining = text or ""
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, max_len + 1)
        if split_at == -1:
            split_at = remaining.rfind(" ", 0, max_len + 1)
        if split_at <= 0:
            split_at = max_len
        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:max_len]
            split_at = max_len
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip("\n ")
    return chunks


async def _send_chunks(message, chunks: Iterable[str], reply_markup=None) -> None:
    first = True
    for chunk in chunks:
        try:
            if first and reply_markup is not None:
                await message.reply_text(chunk, reply_markup=reply_markup)
            else:
                await message.reply_text(chunk)
        except BadRequest as exc:
            if "Message is too long" in str(exc):
                LOGGER.warning("Telegram rejected message chunk as too long; splitting further.")
                for subchunk in chunk_text(chunk, max_len=FALLBACK_CHUNK_SIZE):
                    await message.reply_text(subchunk)
                continue
            LOGGER.exception("Failed to send message chunk: %s", exc)
            break
        first = False


async def safe_send_text(
    update: Update | None,
    context: ContextTypes.DEFAULT_TYPE | None,
    text: str | None,
    reply_markup=None,
) -> int:
    message = update.effective_message if update else None
    if not message:
        return 0
    payload = text if text and text.strip() else EMPTY_MESSAGE_PLACEHOLDER
    await _send_chunks(message, chunk_text(payload, max_len=MAX_CHUNK_SIZE), reply_markup=reply_markup)
    add_response_size(context, len(payload))
    return len(payload)


async def safe_edit_text(
    update: Update | None,
    context: ContextTypes.DEFAULT_TYPE | None,
    text: str | None,
    reply_markup=None,
) -> int:
    message = update.effective_message if update else None
    callback_query = update.callback_query if update else None
    if not message or not callback_query:
        return await safe_send_text(update, context, text, reply_markup=reply_markup)
    payload = text if text and text.strip() else EMPTY_MESSAGE_PLACEHOLDER
    chunks = chunk_text(payload, max_len=MAX_CHUNK_SIZE)
    if not chunks:
        return 0
    try:
        await callback_query.edit_message_text(chunks[0], reply_markup=reply_markup)

    except BadRequest as exc:
        msg = str(exc)
        # Нормально для устаревших кнопок (callback уже протух).
        if "Query is too old" in msg or "response timeout expired" in msg or "query id is invalid" in msg:
            LOGGER.info("Telegram rejected callback edit (expired): %s", msg)
        elif "Message is too long" in msg:
            LOGGER.warning("Telegram rejected edit as too long; falling back to reply_text.")
        else:
            LOGGER.exception("Failed to edit message text: %s", exc)
        # Fallback: ответить реплаем (или, если реплай не выйдет — обычным send_message).
        try:
            await _send_chunks(message, chunks, reply_markup=reply_markup)
        except Exception:
            LOGGER.exception("Fallback reply_text failed; trying bot.send_message")
            if update is not None and context is not None and getattr(update, "effective_chat", None):
                chat_id = update.effective_chat.id
                await safe_send_bot_text(context.bot, chat_id, payload)
        add_response_size(context, len(payload))
        return len(payload)
    if len(chunks) > 1:
        await _send_chunks(message, chunks[1:])
    add_response_size(context, len(payload))
    return len(payload)


async def safe_send_bot_text(bot, chat_id: int, text: str | None) -> int:
    payload = text if text and text.strip() else EMPTY_MESSAGE_PLACEHOLDER
    chunks = chunk_text(payload, max_len=MAX_CHUNK_SIZE)
    sent = 0
    for chunk in chunks:
        try:
            await bot.send_message(chat_id=chat_id, text=chunk)
        except BadRequest as exc:
            if "Message is too long" in str(exc):
                LOGGER.warning("Telegram rejected bot message chunk as too long; splitting further.")
                for subchunk in chunk_text(chunk, max_len=FALLBACK_CHUNK_SIZE):
                    await bot.send_message(chat_id=chat_id, text=subchunk)
                continue
            LOGGER.exception("Failed to send bot message chunk: %s", exc)
            break
        sent += len(chunk)
    return sent


def _selftest() -> None:
    assert chunk_text("", max_len=10) == []
    assert chunk_text("hello", max_len=10) == ["hello"]
    assert chunk_text("hello world", max_len=5) == ["hello", "world"]
    long_text = "a" * 20
    assert chunk_text(long_text, max_len=7) == ["a" * 7, "a" * 7, "a" * 6]
    print("messaging selftest passed")


if __name__ == "__main__":
    _selftest()
