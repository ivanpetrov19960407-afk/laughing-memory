from pathlib import Path

import asyncio

from app.core.orchestrator import Orchestrator
from app.core.text_safety import SAFE_FALLBACK_TEXT
from app.infra.storage import TaskStorage


def test_orchestrator_disabled_task(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    config = {"tasks": {"enabled": ["echo"]}}
    orchestrator = Orchestrator(config=config, storage=storage)

    result = orchestrator.execute_task(user_id=1, task_name="upper", payload="hello")

    assert result.status == "error"
    assert "disabled" in result.text


def test_orchestrator_records_last_execution(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    orchestrator = Orchestrator(config={}, storage=storage)

    result = orchestrator.execute_task(user_id=42, task_name="upper", payload="hello")

    record = storage.get_last_execution(42)
    assert record is not None
    assert record["task_name"] == "upper"
    assert record["status"] == ("success" if result.status == "ok" else "error")


class FakeLLMWithCitations:
    def __init__(self) -> None:
        self.api_key = "fake-key"

    async def create_chat_completion(self, *, model: str, messages: list[dict], max_tokens=None, web_search_options=None):
        return {"content": "HTTP OK.[1][2]"}

    async def generate_text(self, *, model: str, messages: list[dict], max_tokens=None, web_search_options=None) -> str:
        return "HTTP OK.[1][2]"


class FakeLLMWithSourcesOnly:
    def __init__(self) -> None:
        self.api_key = "fake-key"

    async def create_chat_completion(self, *, model: str, messages: list[dict], max_tokens=None, web_search_options=None):
        return {"content": "Источники:\n[1]\n[2]\nпо данным sources"}

    async def generate_text(self, *, model: str, messages: list[dict], max_tokens=None, web_search_options=None) -> str:
        return "Источники:\n[1]\n[2]\nпо данным sources"


def test_orchestrator_ask_no_fake_citations(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    orchestrator = Orchestrator(config={}, storage=storage, llm_client=FakeLLMWithCitations())

    result = asyncio.run(orchestrator.handle("Что такое HTTP?", {"user_id": 1}))

    assert "[" not in result.text
    assert "]" not in result.text
    assert result.sources == []


def test_sanitizer_failure_fallback(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    orchestrator = Orchestrator(config={}, storage=storage, llm_client=FakeLLMWithSourcesOnly())

    result = asyncio.run(orchestrator.handle("Что такое HTTP?", {"user_id": 1}))

    assert result.text == SAFE_FALLBACK_TEXT
