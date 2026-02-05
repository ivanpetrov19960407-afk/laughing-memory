from __future__ import annotations

import asyncio

from app.core.result import Source
from app.tools.web_search import PerplexityWebSearchClient


class FakePerplexity:
    def __init__(self) -> None:
        self.calls = 0

    async def create_chat_completion(self, *, model: str, messages: list[dict], max_tokens=None, web_search_options=None):
        self.calls += 1
        return {
            "content": "ok",
            "citations": [
                "https://example.com/a",
                "https://example.com/b",
                "https://example.com/a",
                "not-a-url",
            ],
        }


class FakeWebSearchClient(PerplexityWebSearchClient):
    async def _build_source(self, url: str) -> Source:  # type: ignore[override]
        return Source(title=f"Title {url}", url=url, snippet="Snippet")


def test_web_search_returns_sources() -> None:
    client = FakeWebSearchClient(FakePerplexity(), model="sonar")

    sources = asyncio.run(client.search("python", max_results=5))

    assert len(sources) == 2
    assert all(isinstance(item.title, str) and isinstance(item.url, str) and isinstance(item.snippet, str) for item in sources)
