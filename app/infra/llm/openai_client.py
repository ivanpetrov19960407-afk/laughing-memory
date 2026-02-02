from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.infra.llm.base import LLMAPIError


class OpenAIAPIError(LLMAPIError):
    """OpenAI-specific API error wrapper."""


class OpenAIClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-3.5-turbo",
        image_model: str = "dall-e-3",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30.0,
        max_retries: int = 1,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.image_model = image_model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    async def create_chat_completion(
        self,
        *,
        model: str | None = None,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
        web_search_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        _ = web_search_options

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
                except httpx.TimeoutException as exc:
                    if attempt < self.max_retries:
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                    raise RuntimeError("OpenAI request timed out") from exc

                if response.status_code >= 500 and attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                break

        if response is None:
            raise RuntimeError("OpenAI request failed")

        if response.status_code // 100 != 2:
            body = response.text
            trimmed = body[:500] + ("..." if len(body) > 500 else "")
            raise OpenAIAPIError(
                status_code=response.status_code,
                message=f"OpenAI API error {response.status_code}: {trimmed}",
            )

        data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return {"content": content}

    async def create_image(
        self,
        *,
        prompt: str,
        size: str = "1024x1024",
        model: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.image_model,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "response_format": "url",
        }
        url = f"{self.base_url}/images/generations"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = None
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(url, json=payload, headers=headers)
                except httpx.TimeoutException as exc:
                    if attempt < self.max_retries:
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                    raise RuntimeError("OpenAI image request timed out") from exc

                if response.status_code >= 500 and attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                break

        if response is None:
            raise RuntimeError("OpenAI image request failed")

        if response.status_code // 100 != 2:
            body = response.text
            trimmed = body[:500] + ("..." if len(body) > 500 else "")
            raise OpenAIAPIError(
                status_code=response.status_code,
                message=f"OpenAI image API error {response.status_code}: {trimmed}",
            )

        data = response.json()
        return data
