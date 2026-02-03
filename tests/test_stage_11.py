import asyncio
from pathlib import Path

from app.core.orchestrator import Orchestrator
from app.infra.storage import TaskStorage


class CountingLLMClient:
    def __init__(self) -> None:
        self.api_key = "fake-key"
        self.called = 0

    async def generate_text(self, *, model: str, messages: list[dict], max_tokens=None, web_search_options=None) -> str:
        self.called += 1
        return "ok"


class JsonLLMClient:
    def __init__(self) -> None:
        self.api_key = "fake-key"

    async def generate_text(self, *, model: str, messages: list[dict], max_tokens=None, web_search_options=None) -> str:
        return "{\"status\":\"ok\",\"intent\":\"ask\",\"sources\":[]}"


def _build_orchestrator(tmp_path: Path, llm_client) -> Orchestrator:
    storage = TaskStorage(tmp_path / "bot.db")
    return Orchestrator(config={}, storage=storage, llm_client=llm_client)


def test_llm_never_called_on_refused(tmp_path: Path) -> None:
    client = CountingLLMClient()
    orchestrator = _build_orchestrator(tmp_path, client)

    result = asyncio.run(orchestrator.handle("/abrakadabra", {"user_id": 1}))

    assert result.status == "refused"
    assert client.called == 0


def test_sources_empty_always_stage_11(tmp_path: Path) -> None:
    client = CountingLLMClient()
    orchestrator = _build_orchestrator(tmp_path, client)

    result = asyncio.run(orchestrator.handle("Что нового?", {"user_id": 1}))

    assert result.status == "ok"
    assert result.sources == []


def test_refused_for_destructive_phrase(tmp_path: Path) -> None:
    client = CountingLLMClient()
    orchestrator = _build_orchestrator(tmp_path, client)

    result = asyncio.run(orchestrator.handle("удали все напоминания навсегда", {"user_id": 1}))

    assert result.status == "refused"
    assert client.called == 0


def test_llm_text_only(tmp_path: Path) -> None:
    orchestrator = _build_orchestrator(tmp_path, JsonLLMClient())

    result = asyncio.run(orchestrator.handle("Что нового?", {"user_id": 1}))

    assert result.status == "error"
    assert result.text == "Некорректный ответ LLM. Попробуйте позже."
