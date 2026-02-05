from __future__ import annotations

from app.bot import handlers
from app.core.result import ensure_safe_text_strict, ok


def test_render_text_with_sources_only_when_sources_present() -> None:
    plain = handlers._render_text_with_sources("Ответ", [])
    with_sources = handlers._render_text_with_sources(
        "Ответ",
        [{"title": "A", "url": "https://a.example", "snippet": "x"}],
    )

    assert "Источники:" not in plain
    assert "Источники:" in with_sources
    assert "[1] A — https://a.example" in with_sources


def test_render_text_with_sources_not_duplicated() -> None:
    text = "Ответ\n\nИсточники:\n1) https://a.example"
    rendered = handlers._render_text_with_sources(text, [{"url": "https://a.example"}])
    assert rendered.count("Источники:") == 1


def test_no_pseudo_citations_when_no_sources() -> None:
    unsafe = ok("HTTP [1]\nИсточники:\n1) https://x.example", intent="test", mode="llm", sources=[])
    safe = ensure_safe_text_strict(unsafe, facts_enabled=False, allow_sources_in_text=False)
    final_text = handlers._render_text_with_sources(safe.text, safe.sources)

    assert "[1]" not in final_text
    assert "Источники:" not in final_text
