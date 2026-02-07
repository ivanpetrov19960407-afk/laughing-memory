from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ChunkScore:
    text: str
    score: int


def split_text(text: str, *, chunk_size: int = 900, overlap: int = 150) -> list[str]:
    if not text:
        return []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks: list[str] = []
    start = 0
    length = len(normalized)
    while start < length:
        end = min(length, start + chunk_size)
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == length:
            break
        start = max(0, end - overlap)
    return chunks


def select_relevant_chunks(
    text: str,
    query: str,
    *,
    chunk_size: int = 900,
    overlap: int = 150,
    top_k: int = 4,
) -> list[str]:
    chunks = split_text(text, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        return []
    tokens = _tokenize(query)
    if not tokens:
        return chunks[:top_k]
    scores: list[ChunkScore] = []
    for chunk in chunks:
        chunk_tokens = _tokenize(chunk)
        score = sum(chunk_tokens.count(token) for token in tokens)
        scores.append(ChunkScore(text=chunk, score=score))
    scores.sort(key=lambda item: item.score, reverse=True)
    if scores and scores[0].score == 0:
        return chunks[:top_k]
    return [item.text for item in scores[:top_k]]


def _tokenize(text: str) -> list[str]:
    raw_tokens = re.findall(r"[\\w\\-]+", text.lower())
    return [token for token in raw_tokens if len(token) >= 3]
