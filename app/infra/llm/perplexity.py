from __future__ import annotations

from typing import Any

import httpx


class PerplexityClient:
    def __init__(self, *, api_key: str, base_url: str = "https://api.perplexity.ai") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def create_chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise RuntimeError(f"Perplexity request failed: {exc}") from exc

        if response.status_code // 100 != 2:
            body = response.text
            trimmed = body[:500] + ("..." if len(body) > 500 else "")
            raise RuntimeError(
                f"Perplexity API error {response.status_code}: {trimmed}"
            )

        data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        citations = data.get("citations")
        return {"content": content, "citations": citations}
