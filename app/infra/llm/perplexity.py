from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class PerplexityAPIError(RuntimeError):
    status_code: int
    message: str

    def __str__(self) -> str:
        return self.message


class PerplexityClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.perplexity.ai",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    async def create_chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
        web_search_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if web_search_options is not None:
            payload["web_search_options"] = web_search_options

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = None
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(url, json=payload, headers=headers)
                except httpx.RequestError as exc:
                    if attempt < self.max_retries:
                        logging.warning(
                            "Perplexity request failed (attempt %s/%s): %s",
                            attempt + 1,
                            self.max_retries + 1,
                            exc,
                        )
                        await asyncio.sleep(1)
                        continue
                    raise RuntimeError(f"Perplexity request failed: {exc}") from exc

                if response.status_code >= 500 and attempt < self.max_retries:
                    logging.warning(
                        "Perplexity API error %s (attempt %s/%s), retrying.",
                        response.status_code,
                        attempt + 1,
                        self.max_retries + 1,
                    )
                    await asyncio.sleep(1)
                    continue
                break

        if response is None:
            raise RuntimeError("Perplexity request failed")

        if response.status_code // 100 != 2:
            body = response.text
            trimmed = body[:500] + ("..." if len(body) > 500 else "")
            raise PerplexityAPIError(
                status_code=response.status_code,
                message=f"Perplexity API error {response.status_code}: {trimmed}",
            )

        data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        citations = data.get("citations")
        return {"content": content, "citations": citations}
