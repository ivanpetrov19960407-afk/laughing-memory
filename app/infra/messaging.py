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


async def _send_chunks(message, chunks: Iterable[str]) -> None:
    for chunk in chunks:
        try:
            await message.reply_text(chunk)
        except BadRequest as exc:
            if "Message is too long" in str(exc):
                LOGGER.warning("Telegram rejected message chunk as too long; splitting further.")
                for subchunk in chunk_text(chunk, max_len=FALLBACK_CHUNK_SIZE):
                    await message.reply_text(subchunk)
                continue
            LOGGER.exception("Failed to send message chunk: %s", exc)
            break


async def safe_send_text(
    update: Update | None,
    context: ContextTypes.DEFAULT_TYPE | None,
    text: str | None,
) -> int:
    message = update.effective_message if update else None
    if not message:
        return 0
    payload = text if text and text.strip() else EMPTY_MESSAGE_PLACEHOLDER
    await _send_chunks(message, chunk_text(payload, max_len=MAX_CHUNK_SIZE))
    add_response_size(context, len(payload))
    return len(payload)


def _selftest() -> None:
    assert chunk_text("", max_len=10) == []
    assert chunk_text("hello", max_len=10) == ["hello"]
    assert chunk_text("hello world", max_len=5) == ["hello", "world"]
    long_text = "a" * 20
    assert chunk_text(long_text, max_len=7) == ["a" * 7, "a" * 7, "a" * 6]
    print("messaging selftest passed")


if __name__ == "__main__":
    _selftest()
