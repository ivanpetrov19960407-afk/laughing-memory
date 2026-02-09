"""Тесты единой идентичности бота: шаблон ответа «кто ты», запрет компаний, поиск без источника."""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.bot_identity import (
    BOT_IDENTITY_DESCRIPTION,
    IDENTITY_ANSWER_TEMPLATE,
    contains_forbidden_identity_mention,
    get_system_prompt_for_llm,
    is_identity_question,
    is_search_query_ambiguous,
)
from app.core.orchestrator import Orchestrator
from app.core.result import Source
from app.infra.storage import TaskStorage


def test_identity_question_detection_ru() -> None:
    assert is_identity_question("Кто ты?") is True
    assert is_identity_question("кто ты такой") is True
    assert is_identity_question("Откуда ты?") is True
    assert is_identity_question("Кто тебя сделал?") is True
    assert is_identity_question("Кто тебя создал?") is True
    assert is_identity_question("ты кто") is True


def test_identity_question_detection_en() -> None:
    assert is_identity_question("Who are you?") is True
    assert is_identity_question("Who made you?") is True
    assert is_identity_question("What are you?") is True


def test_not_identity_question() -> None:
    assert is_identity_question("Что такое HTTP?") is False
    assert is_identity_question("Привет") is False
    assert is_identity_question("") is False
    assert is_identity_question("   ") is False


def test_identity_answer_identical(tmp_path: Path) -> None:
    """Ответ на «Кто ты?» всегда идентичен эталону, без вызова LLM."""
    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(config={}, storage=storage, llm_client=None)
    result = asyncio.run(orchestrator.handle("Кто ты?", {"user_id": 1}))
    assert result.status == "ok"
    assert result.text == IDENTITY_ANSWER_TEMPLATE
    assert result.intent == "identity.local"
    assert result.mode == "local"


def test_identity_answer_who_made_you(tmp_path: Path) -> None:
    """«Кто тебя сделал» тоже даёт эталонный ответ."""
    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(config={}, storage=storage, llm_client=None)
    result = asyncio.run(orchestrator.handle("Кто тебя сделал?", {"user_id": 1}))
    assert result.status == "ok"
    assert result.text == IDENTITY_ANSWER_TEMPLATE


def test_contains_forbidden_identity_mention() -> None:
    assert contains_forbidden_identity_mention("Я Perplexity бот") is True
    assert contains_forbidden_identity_mention("Авандок.ИИ") is True
    assert contains_forbidden_identity_mention("КОРУС Консалтинг") is True
    assert contains_forbidden_identity_mention("локальный российский ИИ") is True
    assert contains_forbidden_identity_mention("OpenAI") is True
    assert contains_forbidden_identity_mention("ChatGPT") is True
    assert contains_forbidden_identity_mention("Обычный ответ без брендов.") is False
    assert contains_forbidden_identity_mention("") is False


def test_llm_response_with_forbidden_mention_refused(tmp_path: Path) -> None:
    """Если LLM вернул текст с упоминанием компании/провайдера — статус refused."""
    class FakeLLMHallucination:
        api_key = "fake"

        async def create_chat_completion(self, *, model: str, messages: list, max_tokens=None, web_search_options=None):
            return {"content": "Я — бот от Perplexity, помогу с поиском."}

        async def generate_text(self, *, model: str, messages: list, max_tokens=None, web_search_options=None) -> str:
            return "Я — бот от Perplexity, помогу с поиском."

    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(config={}, storage=storage, llm_client=FakeLLMHallucination())
    result = asyncio.run(orchestrator.handle("Расскажи про погоду", {"user_id": 1}))
    assert result.status == "refused"
    assert (result.debug or {}).get("reason") == "forbidden_identity_mention"


def test_system_prompt_includes_identity_and_rules() -> None:
    prompt = get_system_prompt_for_llm(extra_instructions="Отвечай кратко.")
    assert BOT_IDENTITY_DESCRIPTION in prompt
    assert "Запрещено" in prompt
    assert "Отвечай кратко" in prompt


def test_search_query_ambiguous() -> None:
    assert is_search_query_ambiguous("а") is True
    assert is_search_query_ambiguous("что") is True
    assert is_search_query_ambiguous("погода") is True  # 1 word, no ?
    assert is_search_query_ambiguous("что такое api") is False
    assert is_search_query_ambiguous("Как настроить кальдарь в телеграме?") is False


def test_search_no_fact_without_source(tmp_path: Path) -> None:
    """Поиск в режиме strict facts без источников — отказ."""
    class FakeSearchEmpty:
        async def search(self, query: str, max_results: int = 5) -> list[Source]:
            return []

    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(
        config={},
        storage=storage,
        llm_client=None,
        search_client=FakeSearchEmpty(),
    )
    result = asyncio.run(
        orchestrator.run_fact_answer(
            user_id=1,
            query="длинный запрос для поиска чтобы не сработал ambiguous",
            facts_only=True,
            intent="command.search",
        )
    )
    assert result.status == "refused"
    assert len(result.sources) == 0


def test_search_facts_only_no_citations_refused(tmp_path: Path) -> None:
    """В режиме strict facts ответ без цитат [N] по источникам — отказ."""
    class FakeSearchOne:
        async def search(self, query: str, max_results: int = 5) -> list[Source]:
            return [Source(title="Example", url="https://example.com", snippet="Some text.")]

    class FakeLLMNoCitations:
        api_key = "fake"

        async def create_chat_completion(self, *, model: str, messages: list, max_tokens=None, web_search_options=None):
            return {"content": "Текст без ссылок на источники."}

        async def generate_text(self, *, model: str, messages: list, max_tokens=None, web_search_options=None) -> str:
            return "Текст без ссылок на источники."

    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(
        config={},
        storage=storage,
        llm_client=FakeLLMNoCitations(),
        search_client=FakeSearchOne(),
    )
    result = asyncio.run(
        orchestrator.run_fact_answer(
            user_id=1,
            query="что такое пример длинный запрос",
            facts_only=True,
            intent="command.search",
        )
    )
    assert result.status == "refused"
    assert result.debug.get("reason") == "facts_no_citations"
