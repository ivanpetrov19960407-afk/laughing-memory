from __future__ import annotations

from telegram.ext import CommandHandler

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
    assert "trace" in commands
    assert "profile" in commands
    assert "history" in commands
