from __future__ import annotations

import html
import logging
import re
from html.parser import HTMLParser
from time import monotonic
from typing import Protocol
from urllib.parse import urlparse

import httpx

from app.core.result import Source
from app.infra.llm.perplexity import PerplexityClient

LOGGER = logging.getLogger(__name__)


class SearchClient(Protocol):
    async def search(self, query: str, max_results: int = 5) -> list[Source]:
        ...


class NullSearchClient:
    async def search(self, query: str, max_results: int = 5) -> list[Source]:
        return []


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): (value or "") for key, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
            return
        if tag.lower() != "meta":
            return
        name = attr_map.get("name", "").lower()
        prop = attr_map.get("property", "").lower()
        if name == "description" or prop in {"og:description", "twitter:description"}:
            if not self.description:
                self.description = attr_map.get("content", "")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.title:
            self.title = data


class PerplexityWebSearchClient:
    def __init__(
        self,
        perplexity_client: PerplexityClient,
        *,
        model: str = "sonar",
        timeout_seconds: float = 4.0,
        snippet_limit: int = 320,
    ) -> None:
        self._perplexity_client = perplexity_client
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._snippet_limit = snippet_limit

    async def search(self, query: str, max_results: int = 5) -> list[Source]:
        if not query.strip():
            return []
        started_at = monotonic()
        response = await self._perplexity_client.create_chat_completion(
            model=self._model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Find reliable web sources for the query and return citations. "
                        f"Query: {query.strip()}"
                    ),
                }
            ],
            web_search_options={"search_context_size": "low"},
        )
        raw_citations = response.get("citations")
        if not isinstance(raw_citations, list):
            return []
        urls = _normalize_urls(raw_citations, max_results=max_results)
        sources = [await self._build_source(url) for url in urls]
        LOGGER.info(
            "Web search: provider=perplexity query_len=%s sources=%s latency=%.2fs",
            len(query),
            len(sources),
            monotonic() - started_at,
        )
        return sources

    async def _build_source(self, url: str) -> Source:
        title = url
        snippet = ""
        html_text = await _fetch_html(url, timeout_seconds=self._timeout_seconds)
        if html_text:
            parsed_title, parsed_description = _parse_title_and_description(html_text)
            if parsed_title:
                title = parsed_title
            snippet = _trim(parsed_description, self._snippet_limit)
        return Source(title=title, url=url, snippet=snippet)


async def _fetch_html(url: str, *, timeout_seconds: float) -> str:
    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "SecretaryBot/1.0 (+web-search)"},
        ) as client:
            response = await client.get(url)
            if response.status_code // 100 != 2:
                return ""
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                return ""
            return response.text[:120_000]
    except Exception:
        LOGGER.debug("Source metadata fetch failed: url=%s", url, exc_info=True)
        return ""


def _normalize_urls(raw_citations: list[object], *, max_results: int) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for item in raw_citations:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if value in seen:
            continue
        seen.add(value)
        urls.append(value)
        if len(urls) >= max(1, max_results):
            break
    return urls


def _parse_title_and_description(html_text: str) -> tuple[str, str]:
    parser = _MetaParser()
    try:
        parser.feed(html_text)
    except Exception:
        pass
    title = _clean(parser.title)
    description = _clean(parser.description)
    if not description:
        match = re.search(r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"']([^\"']+)", html_text, re.IGNORECASE)
        if match:
            description = _clean(match.group(1))
    return title, description


def _clean(value: str) -> str:
    cleaned = html.unescape(value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _trim(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"
