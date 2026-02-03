from __future__ import annotations

from types import SimpleNamespace

from app.bot import handlers
from app.core.result import ok


class DummyContext:
    def __init__(self, strict: bool) -> None:
        self.application = SimpleNamespace(
            bot_data={
                "settings": SimpleNamespace(
                    strict_no_pseudo_sources=strict,
                    enable_menu=True,
                    enable_wizards=False,
                )
            }
        )
        self.chat_data: dict[str, object] = {}


def test_pseudo_sources_blocked_when_no_sources() -> None:
    context = DummyContext(strict=True)
    result = ok("Ответ [1]", intent="test", mode="llm")
    guarded = handlers._apply_pseudo_source_guard(context, result)

    assert guarded.status == "refused"
    assert "источников" in guarded.text.lower()


def test_pseudo_sources_pass_when_clean() -> None:
    context = DummyContext(strict=True)
    result = ok("Обычный ответ без ссылок.", intent="test", mode="llm")
    guarded = handlers._apply_pseudo_source_guard(context, result)

    assert guarded.status == "ok"
    assert guarded.text == "Обычный ответ без ссылок."
