from __future__ import annotations

from app.core.result import Action


def build_menu_actions() -> list[Action]:
    return [
        Action(
            id="calc_help",
            label="🧮 Calc",
            payload={"op": "run_command", "command": "/calc", "args": ""},
        ),
        Action(
            id="calendar_help",
            label="📅 Calendar",
            payload={"op": "run_command", "command": "/calendar", "args": ""},
        ),
        Action(
            id="check_help",
            label="🧠 Check",
            payload={"op": "run_command", "command": "/check", "args": ""},
        ),
        Action(
            id="help",
            label="❓ Help",
            payload={"op": "run_command", "command": "/help", "args": ""},
        ),
        Action(
            id="health",
            label="ℹ️ Статус",
            payload={"op": "run_command", "command": "/health", "args": ""},
        ),
    ]
