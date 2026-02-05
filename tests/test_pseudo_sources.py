from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.bot import actions, handlers
from app.core.result import Attachment, STRICT_REFUSAL_TEXT, ensure_safe_text_strict, ok
from app.infra.rate_limiter import RateLimiter


class DummyContext:
    def __init__(self) -> None:
        self.application = SimpleNamespace(
            bot_data={
                "action_store": actions.ActionStore(),
                "ui_rate_limiter": RateLimiter(),
                "rate_limiter": RateLimiter(),
                "settings": SimpleNamespace(enable_menu=True, enable_wizards=False, strict_no_pseudo_sources=True),
            }
        )
        self.chat_data: dict[str, object] = {}


class DummyUpdate:
    def __init__(self) -> None:
        self.effective_user = SimpleNamespace(id=1)
        self.effective_chat = SimpleNamespace(id=100)
        self.callback_query = None


def test_strict_guard_allows_text_with_real_sources() -> None:
    result = ok(
        "HTTP details [1][2]",
        intent="test",
        mode="llm",
        sources=[{"title": "x", "url": "https://example.com", "snippet": "y"}],
        attachments=[Attachment(type="image", name="x", url="https://example.com")],
    )
    guarded = ensure_safe_text_strict(result, facts_enabled=False, allow_sources_in_text=False)

    assert guarded.status == "ok"
    assert guarded.text == "HTTP details [1][2]"
    assert len(guarded.sources) == 1


@pytest.mark.parametrize(
    ("text",),
    [
        ("по данным ВОЗ",),
        ("according to the report",),
        ("https://example.com",),
        ("example.com",),
    ],
)
def test_strict_guard_blocks_phrases_and_links(text: str) -> None:
    result = ok(text, intent="test", mode="llm")
    guarded = ensure_safe_text_strict(result, facts_enabled=True, allow_sources_in_text=False)

    assert guarded.status == "refused"
    assert guarded.text == STRICT_REFUSAL_TEXT


def test_strict_guard_passes_clean_text() -> None:
    result = ok("Обычный ответ без ссылок.", intent="test", mode="llm")
    guarded = ensure_safe_text_strict(result, facts_enabled=False, allow_sources_in_text=False)

    assert guarded.status == "ok"
    assert guarded.text == "Обычный ответ без ссылок."


def test_handler_applies_strict_guard(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_safe_send_text(update, context, text, reply_markup=None):
        captured["text"] = text
        return len(text or "")

    monkeypatch.setattr(handlers, "safe_send_text", fake_safe_send_text)

    result = ok("Вот ссылка [1].", intent="test", mode="llm")
    asyncio.run(handlers.send_result(DummyUpdate(), DummyContext(), result))

    assert captured["text"] == "Вот ссылка [1]."


def test_strict_guard_cleans_brackets_without_sources_when_facts_off() -> None:
    result = ok("Ответ [1]", intent="test", mode="llm", sources=[])

    guarded = ensure_safe_text_strict(result, facts_enabled=False, allow_sources_in_text=False)

    assert guarded.status == "ok"
    assert "[1]" not in guarded.text


def test_strict_guard_allows_citations_when_sources_present() -> None:
    result = ok(
        "Ответ [1]",
        intent="test",
        mode="llm",
        sources=[{"title": "a", "url": "https://example.com", "snippet": "b"}],
    )

    guarded = ensure_safe_text_strict(result, facts_enabled=True, allow_sources_in_text=False)

    assert guarded.status == "ok"
    assert "[1]" in guarded.text
