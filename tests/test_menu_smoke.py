import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.bot import menu
from app.bot import handlers


def test_menu_actions_use_inline_sections() -> None:
    actions = menu.build_menu_actions(facts_enabled=False, enable_menu=True)
    assert actions
    assert all(
        action.payload.get("op") in {"menu_section", "wizard_cancel", "menu_cancel"} for action in actions
    )


def test_reply_keyboard_markup_not_used() -> None:
    root = Path(__file__).resolve().parents[1]
    sources = (root / "app").rglob("*.py")
    for source in sources:
        assert "ReplyKeyboardMarkup" not in source.read_text(encoding="utf-8")


def test_menu_search_returns_ok() -> None:
    result = asyncio.run(
        handlers._handle_menu_section(
            None,
            section="search",
            user_id=1,
            chat_id=1,
        )
    )
    assert result.status == "ok"


def test_unknown_command_returns_refused(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        return True

    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1, username="tester"),
        effective_chat=SimpleNamespace(id=10),
        message=SimpleNamespace(text="/unknown"),
        effective_message=SimpleNamespace(text="/unknown"),
        callback_query=None,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "settings": SimpleNamespace(
                    enable_menu=True, enable_wizards=False, strict_no_pseudo_sources=True
                ),
                "action_store": SimpleNamespace(),
                "ui_rate_limiter": SimpleNamespace(),
                "rate_limiter": SimpleNamespace(),
                "orchestrator": SimpleNamespace(is_facts_only=lambda user_id: False),
            }
        ),
        chat_data={},
    )

    asyncio.run(handlers.unknown_command(update, context))

    result = captured["result"]
    assert result.status == "refused"
