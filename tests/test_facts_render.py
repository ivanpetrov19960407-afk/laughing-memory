from app.core.facts import format_sources_block, render_fact_response_with_sources
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


def test_format_sources_block_dedupes_by_url() -> None:
    sources = [
        Source(title="First", url="https://same.example", snippet="x"),
        Source(title="Second", url="https://same.example", snippet="y"),
        Source(title="Other", url="https://other.example", snippet="z"),
    ]
    block = format_sources_block(sources)
    assert block.startswith("Источники:")
    assert "[1]" in block
    assert "[2]" in block
    assert "[3]" not in block
    assert "https://same.example" in block
    assert block.count("https://same.example") == 1
    assert "https://other.example" in block
