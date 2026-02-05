from __future__ import annotations

from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler

from app import main


class FakeApplication:
    def __init__(self) -> None:
        self.handlers: list[object] = []

    def add_handler(self, handler: object) -> None:
        self.handlers.append(handler)


def test_facts_handlers_registered() -> None:
    app = FakeApplication()

    main._register_handlers(app)  # type: ignore[arg-type]

    command_handlers = [handler for handler in app.handlers if isinstance(handler, CommandHandler)]
    commands = {command for handler in command_handlers for command in handler.commands}

    assert "facts_on" in commands
    assert "facts_off" in commands


def test_all_critical_handlers_registered() -> None:
    app = FakeApplication()

    main._register_handlers(app)  # type: ignore[arg-type]

    command_handlers = [handler for handler in app.handlers if isinstance(handler, CommandHandler)]
    commands = {command for handler in command_handlers for command in handler.commands}

    expected_commands = {
        "start", "help", "ping", "menu", "cancel",
        "tasks", "task",
        "ask", "search", "summary", "check", "rewrite", "explain", "image",
        "calc", "calendar", "reminders", "reminder_off", "reminder_on",
        "facts_on", "facts_off",
        "context_on", "context_off", "context_clear", "context_status",
        "allow", "deny", "allowlist",
        "health", "selfcheck", "last",
    }
    for cmd in expected_commands:
        assert cmd in commands, f"Missing handler for /{cmd}"


def test_no_duplicate_handlers() -> None:
    app = FakeApplication()

    main._register_handlers(app)  # type: ignore[arg-type]

    command_handlers = [handler for handler in app.handlers if isinstance(handler, CommandHandler)]
    all_commands = [command for handler in command_handlers for command in handler.commands]
    duplicates = [cmd for cmd in all_commands if all_commands.count(cmd) > 1]
    assert duplicates == [], f"Duplicate command handlers: {set(duplicates)}"


def test_callback_and_text_handlers_registered() -> None:
    app = FakeApplication()

    main._register_handlers(app)  # type: ignore[arg-type]

    callback_handlers = [h for h in app.handlers if isinstance(h, CallbackQueryHandler)]
    message_handlers = [h for h in app.handlers if isinstance(h, MessageHandler)]

    assert len(callback_handlers) >= 2, "Need at least static_callback and action_callback"
    assert len(message_handlers) >= 2, "Need at least text handler and unknown command handler"
