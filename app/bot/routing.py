from __future__ import annotations

from app.bot import menu


def normalize_command(text: str) -> str:
    trimmed = (text or "").strip()
    if not trimmed.startswith("/"):
        return ""
    command = trimmed.split(maxsplit=1)[0]
    if "@" in command:
        command = command.split("@", maxsplit=1)[0]
    return command.lower()


def resolve_text_route(text: str) -> str:
    trimmed = (text or "").strip()
    if not trimmed:
        return "empty"
    if trimmed in {
        menu.STATUS_BUTTON,
        menu.SUMMARY_BUTTON,
        menu.FACTS_TOGGLE_BUTTON,
        menu.HELP_BUTTON,
        menu.CHECK_BUTTON,
        menu.REWRITE_BUTTON,
        menu.EXPLAIN_BUTTON,
        menu.CALC_BUTTON,
        menu.CALENDAR_BUTTON,
    }:
        return "menu_button"
    if trimmed.startswith("/"):
        command = normalize_command(trimmed)
        return command.lstrip("/") if command else "command"
    if trimmed.lower().startswith("summary:"):
        return "summary"
    return "llm"
