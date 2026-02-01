import asyncio
from pathlib import Path

from app.core.orchestrator import Orchestrator
from app.infra.storage import TaskStorage


class FakeLLMClient:
    def __init__(self) -> None:
        self.api_key = "fake-key"

    async def create_chat_completion(self, *, model: str, messages: list[dict], max_tokens=None):
        return {"content": "hello from llm"}


def test_orchestrator_ask_llm_success(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    orchestrator = Orchestrator(config={}, storage=storage, llm_client=FakeLLMClient())

    execution = asyncio.run(orchestrator.ask_llm(user_id=7, prompt="hi"))

    assert execution.status == "success"
    assert execution.result == "hello from llm"
    record = storage.get_last_execution(7)
    assert record is not None
    assert record["task_name"] == "ask"


def test_orchestrator_ask_llm_missing_client(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    orchestrator = Orchestrator(config={}, storage=storage, llm_client=None)

    execution = asyncio.run(orchestrator.ask_llm(user_id=1, prompt="hi"))

    assert execution.status == "error"
    assert execution.result == "LLM не настроен: PERPLEXITY_API_KEY"
