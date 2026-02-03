from __future__ import annotations

import re
from typing import Any


SAFE_FALLBACK_TEXT = (
    "Могу объяснить без ссылок на источники. Сформулируй вопрос чуть точнее, и я отвечу."
)

_BRACKET_CITATION_RE = re.compile(r"\[\s*\d{1,3}\s*\]")
_PAREN_CITATION_RE = re.compile(r"\(\s*\d{1,3}\s*\)")
_SOURCES_HEADER_RE = re.compile(r"(?im)^(источники|sources|references)\s*:")
_FORBIDDEN_PHRASES_RE = re.compile(
    r"(?i)\b(источники?|согласно|по данным|references|sources)\b|source:"
)


def sanitize_llm_text(text: str) -> tuple[str, dict[str, Any]]:
    original = text or ""
    working = original
    removal_counts: dict[str, int] = {}

    working, removal_counts["bracket_citations"] = _BRACKET_CITATION_RE.subn("", working)
    working, removal_counts["paren_citations"] = _PAREN_CITATION_RE.subn("", working)

    header_match = _SOURCES_HEADER_RE.search(working)
    truncated_sources_section = False
    if header_match:
        working = working[: header_match.start()].rstrip()
        truncated_sources_section = True

    working, removal_counts["forbidden_phrases"] = _FORBIDDEN_PHRASES_RE.subn("", working)

    working = re.sub(r"[ \t]+", " ", working)
    working = re.sub(r"\s+([,.!?;:])", r"\1", working)
    working = re.sub(r"\n{3,}", "\n\n", working)
    working = working.strip()

    original_len = len(original)
    sanitized_len = len(working)
    removed_chars = max(original_len - sanitized_len, 0)
    removed_ratio = removed_chars / original_len if original_len else 0.0

    forbidden_remaining = bool(
        _BRACKET_CITATION_RE.search(working)
        or _PAREN_CITATION_RE.search(working)
        or _SOURCES_HEADER_RE.search(working)
        or _FORBIDDEN_PHRASES_RE.search(working)
    )

    failed = (
        forbidden_remaining
        or (original_len >= 80 and removed_ratio > 0.30)
        or (original_len > 0 and not working)
    )

    meta: dict[str, Any] = {
        "failed": failed,
        "forbidden_remaining": forbidden_remaining,
        "removed_ratio": removed_ratio,
        "removed_chars": removed_chars,
        "original_len": original_len,
        "sanitized_len": sanitized_len,
        "truncated_sources_section": truncated_sources_section,
        "removal_counts": removal_counts,
    }
    return working, meta
