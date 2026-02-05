from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.orchestrator import Orchestrator
from app.core.result import Source
from app.infra.storage import TaskStorage


class FakeLLM:
    def __init__(self, text: str) -> None:
        self.api_key = "fake"
        self.text = text

    async def create_chat_completion(self, *, model: str, messages: list[dict], max_tokens=None, web_search_options=None):
        return {"content": self.text}

    async def generate_text(self, *, model: str, messages: list[dict], max_tokens=None, web_search_options=None) -> str:
        return self.text


class FakeSearch:
    def __init__(self, sources: list[Source]) -> None:
        self._sources = sources

    async def search(self, query: str, max_results: int = 5) -> list[Source]:
        return self._sources


def test_facts_only_refused_without_sources(tmp_path: Path) -> None:
    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(
        config={},
        storage=storage,
        llm_client=FakeLLM("ignored"),
        search_client=FakeSearch([]),
    )

    result = asyncio.run(orchestrator.run_fact_answer(1, "test", facts_only=True, intent="command.search"))

    assert result.status == "refused"
    assert result.sources == []


def test_search_integration_with_fake_clients(tmp_path: Path) -> None:
    storage = TaskStorage(tmp_path / "bot.db")
    sources = [
        Source(title="A", url="https://a.example", snippet="sa"),
        Source(title="B", url="https://b.example", snippet="sb"),
    ]
    orchestrator = Orchestrator(
        config={},
        storage=storage,
        llm_client=FakeLLM("Ответ по данным [1]."),
        search_client=FakeSearch(sources),
    )

    result = asyncio.run(orchestrator.handle("/search test query", {"user_id": 1}))

    assert result.status == "ok"
    assert len(result.sources) == 2
    assert "Источники:" in result.text
    assert "[1]" in result.text


def test_search_integration_without_results(tmp_path: Path) -> None:
    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(
        config={},
        storage=storage,
        llm_client=FakeLLM("Ответ"),
        search_client=FakeSearch([]),
    )

    result = asyncio.run(orchestrator.handle("/search test query", {"user_id": 1}))

    assert result.status == "refused"
    assert result.sources == []
