from __future__ import annotations

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.infra.messaging import safe_send_text

STATUS_BUTTON = "ℹ️ Статус"
SUMMARY_BUTTON = "🧾 Summary"
FACTS_TOGGLE_BUTTON = "🔎 Facts ON/OFF"
HELP_BUTTON = "❓ Help"


def build_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [STATUS_BUTTON, SUMMARY_BUTTON],
            [FACTS_TOGGLE_BUTTON, HELP_BUTTON],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


async def show_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str | None = None,
) -> None:
    message = text or "Меню:"
    await safe_send_text(update, context, message, reply_markup=build_main_menu())
