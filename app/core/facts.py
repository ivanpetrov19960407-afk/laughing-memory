from __future__ import annotations

from app.core.result import Source


def render_fact_response_with_sources(text: str, sources: list[Source]) -> str:
    if not sources:
        return text
    lines = [text.rstrip(), "", "Источники:"]
    for index, source in enumerate(sources, start=1):
        title = _trim(source.title or source.url, 140)
        url = source.url.strip()
        lines.append(f"[{index}] {title} — {url}")
    return "\n".join(lines).strip()


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
