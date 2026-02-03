from __future__ import annotations

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
    if trimmed.startswith("/"):
        command = normalize_command(trimmed)
        return command.lstrip("/") if command else "command"
    if trimmed.lower().startswith("summary:"):
        return "summary"
    return "llm"
