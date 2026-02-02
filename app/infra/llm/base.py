from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMAPIError(RuntimeError):
    status_code: int
    message: str

    def __str__(self) -> str:
        return self.message


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
