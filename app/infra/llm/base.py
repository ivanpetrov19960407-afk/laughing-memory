from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMAPIError(RuntimeError):
    status_code: int
    message: str

    def __str__(self) -> str:
        return self.message


class LLMGuardError(ValueError):
    """Raised when LLM response violates plain-text guardrails."""


class LLMClient(Protocol):
    api_key: str

    async def create_chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
        web_search_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    async def generate_text(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
        web_search_options: dict[str, Any] | None = None,
    ) -> str:
        ...


def ensure_plain_text(text: str) -> str:
    trimmed = text.strip()
    lowered = trimmed.lower()
    if not trimmed:
        return ""
    if trimmed.startswith("{") or trimmed.startswith("["):
        raise LLMGuardError("LLM returned JSON-like content.")
    forbidden_markers = ("\"status\"", "\"intent\"", "\"sources\"", "\"actions\"")
    if any(marker in lowered for marker in forbidden_markers):
        raise LLMGuardError("LLM returned forbidden fields.")
    return text
