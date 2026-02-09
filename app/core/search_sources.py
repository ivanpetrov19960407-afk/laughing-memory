"""Search source definitions and enabled list for a user."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SearchSource:
    id: str
    name: str
    priority: int


DEFAULT_SOURCES: list[SearchSource] = [
    SearchSource(id="perplexity", name="Perplexity", priority=1),
    SearchSource(id="backup", name="Резерв", priority=2),
]


def parse_sources_from_config(config: dict[str, Any]) -> list[SearchSource]:
    """Parse search_sources list from orchestrator-style config. Returns DEFAULT_SOURCES if missing."""
    raw = config.get("search_sources")
    if not isinstance(raw, list) or not raw:
        return list(DEFAULT_SOURCES)
    result: list[SearchSource] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        name = item.get("name")
        priority = item.get("priority")
        if not isinstance(sid, str) or not sid.strip():
            continue
        result.append(
            SearchSource(
                id=sid.strip(),
                name=str(name).strip() if isinstance(name, str) else sid.strip(),
                priority=int(priority) if isinstance(priority, (int, float)) else len(result) + 1,
            )
        )
    if not result:
        return list(DEFAULT_SOURCES)
    result.sort(key=lambda s: (s.priority, s.id))
    return result


def get_enabled_sources(
    sources: list[SearchSource],
    user_disabled: set[str],
) -> list[SearchSource]:
    """Return sources that are enabled for the user, in priority order."""
    return [s for s in sources if s.id not in user_disabled]
