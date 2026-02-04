from pathlib import Path

from app.bot import menu


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
