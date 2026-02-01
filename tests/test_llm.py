import asyncio
from datetime import datetime, timezone
from pathlib import Path

from app.core.orchestrator import Orchestrator
from app.core.models import TaskExecutionResult
from app.infra.access import AccessController
from app.infra.rate_limit import RateLimiter
from app.infra.storage import TaskStorage


class FakeLLMClient:
    def __init__(self) -> None:
        self.api_key = "fake-key"

    async def create_chat_completion(self, *, model: str, messages: list[dict], max_tokens=None):
        return {"content": "hello from llm"}


class CaptureLLMClient:
    def __init__(self) -> None:
        self.api_key = "fake-key"
        self.last_messages: list[dict] = []

    async def create_chat_completion(self, *, model: str, messages: list[dict], max_tokens=None):
        self.last_messages = messages
        return {"content": "ok"}


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


def test_orchestrator_whitelist_blocks(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    access = AccessController({7})
    orchestrator = Orchestrator(
        config={},
        storage=storage,
        llm_client=FakeLLMClient(),
        access=access,
    )

    execution = asyncio.run(orchestrator.ask_llm(user_id=1, prompt="hi"))

    assert execution.status == "error"
    assert "Доступ запрещ" in execution.result


def test_orchestrator_rate_limit_blocks_second_request(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    rate_limiter = RateLimiter(per_minute=1, per_day=999)
    orchestrator = Orchestrator(
        config={},
        storage=storage,
        llm_client=FakeLLMClient(),
        rate_limiter=rate_limiter,
    )

    first = asyncio.run(orchestrator.ask_llm(user_id=1, prompt="hi"))
    second = asyncio.run(orchestrator.ask_llm(user_id=1, prompt="hi again"))

    assert first.status == "success"
    assert second.status == "error"
    assert "Лимит запросов" in second.result


def test_orchestrator_context_appends_history(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    client = CaptureLLMClient()
    orchestrator = Orchestrator(
        config={},
        storage=storage,
        llm_client=client,
        llm_history_turns=2,
    )

    storage.record_execution(
        TaskExecutionResult(
            task_name="ask",
            payload="hi",
            result="hello",
            status="success",
            executed_at=datetime.now(timezone.utc),
            user_id=1,
        )
    )
    storage.record_execution(
        TaskExecutionResult(
            task_name="ask",
            payload="how are you",
            result="fine",
            status="success",
            executed_at=datetime.now(timezone.utc),
            user_id=1,
        )
    )

    asyncio.run(orchestrator.ask_llm(user_id=1, prompt="new message"))

    assert client.last_messages == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "how are you"},
        {"role": "assistant", "content": "fine"},
        {"role": "user", "content": "new message"},
    ]
