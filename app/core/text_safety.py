from __future__ import annotations

import re
from typing import Any


SAFE_FALLBACK_TEXT = (
    "Могу объяснить без ссылок на источники. Сформулируй вопрос чуть точнее, и я отвечу."
)
SOURCES_DISCLAIMER_TEXT = "Это обобщённое объяснение без ссылок на исследования и без строгой доказательной базы."

_BRACKET_CITATION_RE = re.compile(r"\[\s*\d{1,3}\s*\]")
_PAREN_CITATION_RE = re.compile(r"\(\s*\d{1,3}\s*\)")
_SOURCES_HEADER_RE = re.compile(r"(?im)^(источники|sources|references)\s*:")
_FORBIDDEN_PHRASES_RE = re.compile(
    r"(?i)\b(источники?|согласно|по данным|references|sources|doi|pubmed|arxiv)\b|source:"
)
_URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_WWW_RE = re.compile(r"\bwww\.[^\s<>]+", re.IGNORECASE)
_DOMAIN_RE = re.compile(
    r"\b[a-z0-9.-]+\.(?:com|org|net|edu|gov|io|ru|uk|de|fr|es|it|nl|jp|cn|info|biz)\b",
    re.IGNORECASE,
)
_DOI_RE = re.compile(r"\bdoi:\s*\S+", re.IGNORECASE)
_SOURCES_REQUEST_RE = re.compile(
    r"(?i)\b("
    r"ссылк\w*|источник\w*|исследован\w*|доказательств\w*|по данным|согласно|"
    r"study|studies|research|source|citation|references|peer[-\s]?reviewed|"
    r"doi|pubmed|arxiv"
    r")\b"
)
_STRICT_BLOCK_RE = re.compile(
    r"(?i)\b("
    r"исследован\w*|исследовани\w*|доказательств\w*|подтвержден\w*|по данным|согласно|"
    r"мета-?анализ|рандомизирован\w*|двойн\w* слеп\w*|контрольн\w* групп\w*|"
    r"уч[её]н\w* выяснил\w*|в исследовани\w*|"
    r"study|studies|research|peer[-\s]?reviewed|randomized|double[-\s]?blind|"
    r"control group|meta-?analysis|statistically значим\w*|statistically significant|"
    r"p-?value|fMRI|MRI|МРТ|антидот|эндорфин\w*|n\s*=\s*\d+|"
    r"percent|процент\w*"
    r")\b"
)
_HAS_DIGIT_RE = re.compile(r"\d")


def is_sources_request(text: str) -> bool:
    return bool(_SOURCES_REQUEST_RE.search(text or ""))


def _normalize_text(text: str) -> str:
    working = re.sub(r"[ \t]+", " ", text)
    working = re.sub(r"\s+([,.!?;:])", r"\1", working)
    working = re.sub(r"\n{3,}", "\n\n", working)
    return working.strip()


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    return [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", text) if segment.strip()]


def sanitize_llm_text(text: str, *, sources_requested: bool = False) -> tuple[str, dict[str, Any]]:
    original = text or ""
    working = original
    removal_counts: dict[str, int] = {}

    working, removal_counts["urls"] = _URL_RE.subn("", working)
    working, removal_counts["www"] = _WWW_RE.subn("", working)
    working, removal_counts["domains"] = _DOMAIN_RE.subn("", working)
    working, removal_counts["doi"] = _DOI_RE.subn("", working)
    working, removal_counts["bracket_citations"] = _BRACKET_CITATION_RE.subn("", working)
    working, removal_counts["paren_citations"] = _PAREN_CITATION_RE.subn("", working)

    header_match = _SOURCES_HEADER_RE.search(working)
    truncated_sources_section = False
    if header_match:
        working = working[: header_match.start()].rstrip()
        truncated_sources_section = True

    working, removal_counts["forbidden_phrases"] = _FORBIDDEN_PHRASES_RE.subn("", working)

    strict_removed_sentences = 0
    if sources_requested:
        sentences = _split_sentences(working)
        kept: list[str] = []
        for sentence in sentences:
            if _STRICT_BLOCK_RE.search(sentence) or _HAS_DIGIT_RE.search(sentence):
                strict_removed_sentences += 1
                continue
            if not sentence.strip():
                continue
            kept.append(sentence.strip())
        working = " ".join(kept)

    working = _normalize_text(working)

    original_len = len(original)
    sanitized_len = len(working)
    removed_chars = max(original_len - sanitized_len, 0)
    removed_ratio = removed_chars / original_len if original_len else 0.0

    if sources_requested:
        content = working
        if content and content.startswith(SOURCES_DISCLAIMER_TEXT):
            content = content[len(SOURCES_DISCLAIMER_TEXT) :].strip()
    else:
        content = working
    sentence_count = len(_split_sentences(content))
    needs_regeneration = bool(
        sources_requested and (not content or sentence_count < 2 or len(content) < 40)
    )

    disclaimer_added = False
    if sources_requested:
        if not working.startswith(SOURCES_DISCLAIMER_TEXT):
            working = f"{SOURCES_DISCLAIMER_TEXT}\n{working}".strip() if working else SOURCES_DISCLAIMER_TEXT
            disclaimer_added = True

    forbidden_remaining = bool(
        _BRACKET_CITATION_RE.search(working)
        or _PAREN_CITATION_RE.search(working)
        or _SOURCES_HEADER_RE.search(working)
        or _FORBIDDEN_PHRASES_RE.search(working)
        or _URL_RE.search(working)
        or _WWW_RE.search(working)
        or _DOMAIN_RE.search(working)
        or _DOI_RE.search(working)
        or (sources_requested and (_STRICT_BLOCK_RE.search(content) or _HAS_DIGIT_RE.search(content)))
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
        "strict_removed_sentences": strict_removed_sentences,
        "disclaimer_added": disclaimer_added,
        "needs_regeneration": needs_regeneration,
        "sources_requested": sources_requested,
    }
    return working, meta
