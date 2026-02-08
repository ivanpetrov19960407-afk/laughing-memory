import asyncio
from datetime import datetime, timezone
from pathlib import Path

from app.core.orchestrator import Orchestrator
from app.core.models import TaskExecutionResult
from app.core.memory_layers import build_memory_layers_context
from app.core.memory_manager import MemoryManager, UserProfileMemory
from app.infra.access import AccessController
from app.infra.rate_limit import RateLimiter
from app.infra.request_context import RequestContext
from app.infra.storage import TaskStorage
from app.infra.user_profile_store import UserProfileStore


class FakeLLMClient:
    def __init__(self) -> None:
        self.api_key = "fake-key"

    async def create_chat_completion(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens=None,
        web_search_options=None,
    ):
        return {"content": "hello from llm"}

    async def generate_text(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens=None,
        web_search_options=None,
    ) -> str:
        return "hello from llm"


class CaptureLLMClient:
    def __init__(self) -> None:
        self.api_key = "fake-key"
        self.last_messages: list[dict] = []

    async def create_chat_completion(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens=None,
        web_search_options=None,
    ):
        self.last_messages = messages
        return {"content": "ok"}

    async def generate_text(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens=None,
        web_search_options=None,
    ) -> str:
        self.last_messages = messages
        return "ok"


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
    assert execution.result == "LLM не настроен: OPENAI_API_KEY или PERPLEXITY_API_KEY"


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
    assert "Слишком часто" in second.result


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
        {
            "role": "system",
            "content": (
                "Ответь только текстом. Не возвращай JSON, поля, статус, intent, sources, actions.\n"
                "Запрещено использовать ссылки, цитаты, номера источников, квадратные/круглые скобки "
                "с цифрами (например [1], [2], (1)), фразы \"по данным\", \"согласно\", \"источник:\", "
                "\"references\". Если источники не были предоставлены отдельно, не упоминай источники вовсе. "
                "Верни только связный текст без упоминаний источников."
            ),
        },
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "how are you"},
        {"role": "assistant", "content": "fine"},
        {"role": "user", "content": "new message"},
    ]


def test_orchestrator_uses_memory_context(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    client = CaptureLLMClient()
    orchestrator = Orchestrator(config={}, storage=storage, llm_client=client)
    profile_store = UserProfileStore(tmp_path / "profiles.db")
    profile_store.update(1, {"language": "en", "timezone": "Europe/London", "verbosity": "short"})
    request_context = RequestContext(
        correlation_id="corr-ctx",
        user_id=1,
        chat_id=2,
        message_id=3,
        timezone=None,
        ts=datetime.now(timezone.utc),
        env="dev",
    )
    memory_manager = MemoryManager(dialog=None, profile=UserProfileMemory(profile_store), actions=None)
    memory_context = asyncio.run(
        build_memory_layers_context(
            request_context,
            memory_manager=memory_manager,
            max_chars=500,
        )
    )

    assert memory_context is not None
    asyncio.run(orchestrator.handle("/ask Привет", {"user_id": 1, "memory_context": memory_context}))

    last_message = client.last_messages[-1]["content"]
    assert "Профиль пользователя" in last_message
    assert "язык: en" in last_message
    assert last_message.endswith("Привет")
