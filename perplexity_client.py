from __future__ import annotations

import os
from typing import Any

import httpx


class PerplexityRequestError(RuntimeError):
    pass


async def ask_perplexity(
    messages: list[dict[str, Any]],
    model: str = "sonar",
    timeout: float = 60.0,
) -> tuple[str, list[str]]:
    api_key = os.getenv("PERPLEXITY_API_KEY")
    if not api_key:
        raise PerplexityRequestError("PERPLEXITY_API_KEY is not set")

    url = "https://api.perplexity.ai/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "messages": messages}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise PerplexityRequestError("Perplexity request timed out") from exc
    except httpx.HTTPError as exc:
        raise PerplexityRequestError("Perplexity request failed") from exc

    if response.status_code // 100 != 2:
        body = response.text.strip()
        trimmed = body[:500] + ("..." if len(body) > 500 else "")
        raise PerplexityRequestError(
            f"Perplexity API error {response.status_code}: {trimmed}"
        )

    data = response.json()
    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    citations = data.get("citations") or []
    if not isinstance(citations, list):
        citations = []
    citations_list = [str(item) for item in citations if item]
    return content, citations_list
