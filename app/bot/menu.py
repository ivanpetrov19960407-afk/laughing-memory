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
    "💬 Чат",
    "🧮 Калькулятор",
    "📅 Календарь",
    "⏰ Напоминания",
    "⚙️ Настройки",
    "❌ Отмена",
    "🏠 Меню",
    "📌 Режим фактов",
    "🧹 Очистить контекст",
    "🧮 Посчитать",
    "➕ Добавить",
    "📋 Список",
    "➕ Создать",
    "📌 Факты on",
    "📌 Факты off",
    "🧠 Контекст on",
    "🧠 Контекст off",
    "ℹ️ Примеры",
    "🔎 Поиск",
}


def build_menu_actions(*, facts_enabled: bool, enable_menu: bool) -> list[Action]:
    if not enable_menu:
        return build_legacy_menu_actions(facts_enabled=facts_enabled)
    return [
        Action(id="menu.chat", label="💬 Чат", payload={"op": "menu_section", "section": "chat"}),
        Action(id="menu.calc", label="🧮 Калькулятор", payload={"op": "menu_section", "section": "calc"}),
        Action(id="menu.calendar", label="📅 Календарь", payload={"op": "menu_section", "section": "calendar"}),
        Action(id="menu.reminders", label="⏰ Напоминания", payload={"op": "menu_section", "section": "reminders"}),
        Action(id="menu.settings", label="⚙️ Настройки", payload={"op": "menu_section", "section": "settings"}),
        Action(id="menu.search", label="🔎 Поиск", payload={"op": "menu_section", "section": "search"}),
        Action(id="menu.home", label="🏠 Меню", payload={"op": "menu_section", "section": "home"}),
    ]


def build_legacy_menu_actions(*, facts_enabled: bool) -> list[Action]:
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
