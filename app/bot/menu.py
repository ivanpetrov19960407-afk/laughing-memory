from __future__ import annotations

from telegram import ReplyKeyboardMarkup

STATUS_BUTTON = "ℹ️ Статус"
SUMMARY_BUTTON = "🧾 Summary"
FACTS_TOGGLE_BUTTON = "🔎 Facts ON/OFF"
HELP_BUTTON = "❓ Help"
CHECK_BUTTON = "🧠 Check"
REWRITE_BUTTON = "✍️ Rewrite"
EXPLAIN_BUTTON = "🧩 Explain"
CALC_BUTTON = "🧮 Calc"
CALENDAR_BUTTON = "📅 Calendar"


def build_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [STATUS_BUTTON, SUMMARY_BUTTON],
            [FACTS_TOGGLE_BUTTON, HELP_BUTTON],
            [CHECK_BUTTON, REWRITE_BUTTON],
            [EXPLAIN_BUTTON, CALC_BUTTON],
            [CALENDAR_BUTTON],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def build_menu_payload(text: str | None = None) -> tuple[str, ReplyKeyboardMarkup]:
    message = text or "Меню:"
    return message, build_main_menu()
