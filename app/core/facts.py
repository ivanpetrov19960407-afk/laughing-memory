from __future__ import annotations

from app.core.result import Source


def render_fact_response_with_sources(text: str, sources: list[Source]) -> str:
    if not sources:
        return text
    block = format_sources_block(sources)
    return "\n".join([text.rstrip(), "", block]).strip()


def format_sources_block(sources: list[Source]) -> str:
    if not sources:
        return "Источники:\n(нет)"
    lines = ["Источники:"]
    for index, source in enumerate(sources, start=1):
        title = _trim(source.title or source.url, 140)
        url = (source.url or "").strip()
        if url:
            lines.append(f"[{index}] {title} — {url}")
        else:
            lines.append(f"[{index}] {title}")
    return "\n".join(lines)


def build_sources_prompt(sources: list[Source]) -> str:
    lines = ["Источники:"]
    for index, source in enumerate(sources, start=1):
        title = _trim(source.title or source.url, 140)
        snippet = _trim(source.snippet, 400)
        lines.append(f"[{index}] {title} — {source.url}")
        if snippet:
            lines.append(f"    {snippet}")
    return "\n".join(lines)


def _trim(value: str, limit: int) -> str:
    cleaned = (value or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"
