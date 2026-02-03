from app.bot import menu


def test_menu_actions_use_inline_sections() -> None:
    actions = menu.build_menu_actions(facts_enabled=False, enable_menu=True)
    assert actions
    assert all(action.payload.get("op") == "menu_section" or action.payload.get("op") == "wizard_cancel" for action in actions)
