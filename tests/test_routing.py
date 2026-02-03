from app.bot.routing import resolve_text_route


def test_plain_question_no_reminders_leak() -> None:
    route = resolve_text_route("Что такое эффект плацебо?")

    assert route == "llm"
