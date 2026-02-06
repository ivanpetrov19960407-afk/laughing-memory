"""Tests for strict facts mode, sources format stability, and full search cycle."""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.orchestrator import Orchestrator
from app.core.result import Source, OrchestratorResult, ensure_valid
from app.core.facts import build_sources_prompt, render_fact_response_with_sources
from app.infra.storage import TaskStorage


class FakeLLM:
    def __init__(self, text: str) -> None:
        self.api_key = "fake"
        self.text = text

    async def generate_text(self, *, model: str, messages: list[dict], max_tokens=None, web_search_options=None) -> str:
        return self.text


class FakeSearch:
    def __init__(self, sources: list[Source]) -> None:
        self._sources = sources

    async def search(self, query: str, max_results: int = 5) -> list[Source]:
        return self._sources


class EmptySearch:
    async def search(self, query: str, max_results: int = 5) -> list[Source]:
        return []


# ---- Strict facts mode: refused when sources empty ----

def test_strict_facts_refused_no_sources(tmp_path: Path) -> None:
    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(
        config={},
        storage=storage,
        llm_client=FakeLLM("Some answer"),
        search_client=EmptySearch(),
    )

    result = asyncio.run(
        orchestrator.run_fact_answer(1, "what is Python", facts_only=True, intent="command.search")
    )
    assert result.status == "refused"
    assert result.sources == []
    assert result.text.strip() != ""


def test_strict_facts_does_not_invent_answer(tmp_path: Path) -> None:
    """In facts-only mode, if search returns nothing, bot must refuse - no LLM answer."""
    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(
        config={},
        storage=storage,
        llm_client=FakeLLM("I know everything"),
        search_client=EmptySearch(),
    )

    result = asyncio.run(
        orchestrator.run_fact_answer(1, "test", facts_only=True, intent="search")
    )
    assert result.status == "refused"
    assert "I know everything" not in result.text


# ---- Sources format stability ----

def test_source_has_required_fields() -> None:
    source = Source(title="Test", url="https://example.com", snippet="A snippet")
    assert isinstance(source.title, str)
    assert isinstance(source.url, str)
    assert isinstance(source.snippet, str)


def test_sources_list_format_in_result() -> None:
    sources = [
        Source(title="A", url="https://a.example", snippet="sa"),
        Source(title="B", url="https://b.example", snippet="sb"),
    ]
    result = OrchestratorResult(
        text="Answer",
        status="ok",
        mode="llm",
        intent="search",
        sources=sources,
    )
    result.validate()
    public = result.to_public_dict()
    assert len(public["sources"]) == 2
    for src in public["sources"]:
        assert "title" in src
        assert "url" in src
        assert "snippet" in src


def test_build_sources_prompt_format() -> None:
    sources = [
        Source(title="Title A", url="https://a.example", snippet="Snippet A"),
        Source(title="Title B", url="https://b.example", snippet="Snippet B"),
    ]
    prompt = build_sources_prompt(sources)
    assert "Источники:" in prompt
    assert "[1]" in prompt
    assert "[2]" in prompt
    assert "https://a.example" in prompt
    assert "https://b.example" in prompt


def test_render_fact_response_appends_sources() -> None:
    sources = [
        Source(title="A", url="https://a.example", snippet="sa"),
    ]
    rendered = render_fact_response_with_sources("Answer text [1].", sources)
    assert "Источники:" in rendered
    assert "https://a.example" in rendered
    assert "[1]" in rendered


def test_render_fact_response_no_sources_unchanged() -> None:
    rendered = render_fact_response_with_sources("Plain answer.", [])
    assert rendered == "Plain answer."


# ---- Full search cycle: search -> LLM -> text + sources ----

def test_full_search_cycle(tmp_path: Path) -> None:
    storage = TaskStorage(tmp_path / "bot.db")
    sources = [
        Source(title="Wikipedia", url="https://en.wikipedia.org/wiki/Python", snippet="Python is a language"),
        Source(title="Docs", url="https://docs.python.org", snippet="Official docs"),
    ]
    orchestrator = Orchestrator(
        config={},
        storage=storage,
        llm_client=FakeLLM("Python — язык программирования [1]. Документация доступна [2]."),
        search_client=FakeSearch(sources),
    )

    result = asyncio.run(
        orchestrator.handle("/search what is Python", {"user_id": 1})
    )

    assert result.status == "ok"
    assert len(result.sources) == 2
    assert "Источники:" in result.text
    assert "[1]" in result.text
    assert "[2]" in result.text
    assert "https://en.wikipedia.org" in result.text
    assert "https://docs.python.org" in result.text


def test_search_disabled_returns_refused(tmp_path: Path) -> None:
    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(
        config={},
        storage=storage,
        llm_client=FakeLLM("answer"),
        feature_web_search=False,
    )

    result = asyncio.run(
        orchestrator.run_fact_answer(1, "test", facts_only=True, intent="search")
    )
    assert result.status == "refused"
    assert "отключён" in result.text.lower()


def test_ensure_valid_strips_fake_citations_from_search_result() -> None:
    """When LLM hallucinates citations but result has no real sources,
    ensure_valid must strip them."""
    result = OrchestratorResult(
        text="According to [1] source [2].",
        status="ok",
        mode="llm",
        intent="ask",
        sources=[],
    )
    validated = ensure_valid(result)
    assert "[1]" not in validated.text
    assert "[2]" not in validated.text
