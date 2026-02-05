from app.core.facts import render_fact_response_with_sources
from app.core.result import Source


def test_render_fact_response_with_sources() -> None:
    text = "Ответ с фактами [1]."
    sources = [
        Source(title="Source A", url="https://a.example", snippet="a"),
        Source(title="Source B", url="https://b.example", snippet="b"),
    ]

    rendered = render_fact_response_with_sources(text, sources)

    assert rendered.startswith(text)
    assert "Источники:" in rendered
    assert "[1] Source A — https://a.example" in rendered
    assert "[2] Source B — https://b.example" in rendered
