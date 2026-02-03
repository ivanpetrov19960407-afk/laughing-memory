from __future__ import annotations

from app.core.result import Action


MENU_LABELS = {
    "🧮 Calc",
    "📅 Calendar",
    "✅ Check",
    "🧠 Check",
    "❓ Help",
    "ℹ️ Status",
    "ℹ️ Статус",
    "⏰ Reminders",
    "🧾 Summary",
    "🔎 Facts ON",
    "🔎 Facts OFF",
}


def build_menu_actions(*, facts_enabled: bool) -> list[Action]:
    facts_label = "🔎 Facts OFF" if facts_enabled else "🔎 Facts ON"
    facts_command = "/facts_off" if facts_enabled else "/facts_on"
    return [
        Action(
            id="menu.status",
            label="ℹ️ Status",
            payload={"op": "run_command", "command": "/health", "args": ""},
        ),
        Action(
            id="menu.help",
            label="❓ Help",
            payload={"op": "run_command", "command": "/help", "args": ""},
        ),
        Action(
            id="menu.calc",
            label="🧮 Calc",
            payload={"op": "run_command", "command": "/calc", "args": ""},
        ),
        Action(
            id="menu.calendar",
            label="📅 Calendar",
            payload={"op": "run_command", "command": "/calendar", "args": ""},
        ),
        Action(
            id="menu.reminders",
            label="⏰ Reminders",
            payload={"op": "run_command", "command": "/reminders", "args": ""},
        ),
        Action(
            id="menu.facts",
            label=facts_label,
            payload={"op": "run_command", "command": facts_command, "args": ""},
        ),
        Action(
            id="menu.summary",
            label="🧾 Summary",
            payload={"op": "run_command", "command": "/summary", "args": ""},
        ),
        Action(
            id="menu.check",
            label="✅ Check",
            payload={"op": "run_command", "command": "/check", "args": ""},
        ),
    ]


def is_menu_label(text: str) -> bool:
    return text.strip() in MENU_LABELS
