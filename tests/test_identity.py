"""
Тесты единой идентичности бота: без галлюцинаций, канонический ответ на «кто ты», поиск только по источникам.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.identity import (
    CANONICAL_IDENTITY_ANSWER,
    contains_forbidden_identity_mention,
    is_identity_question,
)
from app.core.orchestrator import Orchestrator
from app.core.result import Source
from app.infra.storage import TaskStorage


def test_is_identity_question_ru() -> None:
    assert is_identity_question("Кто ты?") is True
    assert is_identity_question("кто ты") is True
    assert is_identity_question("Откуда ты?") is True
    assert is_identity_question("Кто тебя сделал?") is True
    assert is_identity_question("Как тебя зовут?") is True


def test_is_identity_question_en() -> None:
    assert is_identity_question("Who are you?") is True
    assert is_identity_question("What are you?") is True
    assert is_identity_question("Who created you?") is True


def test_is_identity_question_negative() -> None:
    assert is_identity_question("Что такое HTTP?") is False
    assert is_identity_question("Привет") is False
    assert is_identity_question("") is False
    assert is_identity_question("Расскажи про погоду") is False


def test_contains_forbidden_identity_mention() -> None:
    assert contains_forbidden_identity_mention("Я от Авандок.ИИ") is True
    assert contains_forbidden_identity_mention("КОРУС Консалтинг") is True
    assert contains_forbidden_identity_mention("Perplexity нет") is True
    assert contains_forbidden_identity_mention("локальный российский ИИ") is True
    assert contains_forbidden_identity_mention("OpenAI") is True
    assert contains_forbidden_identity_mention("ChatGPT") is True


def test_contains_forbidden_identity_mention_negative() -> None:
    assert contains_forbidden_identity_mention("Я бот-оркестратор.") is False
    assert contains_forbidden_identity_mention(CANONICAL_IDENTITY_ANSWER) is False
    assert contains_forbidden_identity_mention("") is False


def test_canonical_identity_answer_constant() -> None:
    assert "Telegram-бот-оркестратор" in CANONICAL_IDENTITY_ANSWER
    assert "задач и инструментов" in CANONICAL_IDENTITY_ANSWER
    assert "/menu" in CANONICAL_IDENTITY_ANSWER
    assert "Авандок" not in CANONICAL_IDENTITY_ANSWER
    assert "КОРУС" not in CANONICAL_IDENTITY_ANSWER
    assert "Perplexity" not in CANONICAL_IDENTITY_ANSWER


class FakeLLMIdentityHallucination:
    """LLM, возвращающий запрещённую идентичность."""

    def __init__(self) -> None:
        self.api_key = "fake"

    async def create_chat_completion(self, *, model: str, messages: list, max_tokens=None, web_search_options=None):
        return {"content": "Я Авандок.ИИ, локальный российский ИИ от КОРУС."}

    async def generate_text(self, *, model: str, messages: list, max_tokens=None, web_search_options=None) -> str:
        return "Я Авандок.ИИ, локальный российский ИИ от КОРУС."


class FakeLLMNormal:
    def __init__(self, text: str = "Обычный ответ.") -> None:
        self.api_key = "fake"
        self.text = text

    async def create_chat_completion(self, *, model: str, messages: list, max_tokens=None, web_search_options=None):
        return {"content": self.text}

    async def generate_text(self, *, model: str, messages: list, max_tokens=None, web_search_options=None) -> str:
        return self.text


def test_who_are_you_returns_canonical_answer(tmp_path: Path) -> None:
    """Ответ на «кто ты» всегда идентичен эталону, без вызова LLM."""
    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(config={}, storage=storage, llm_client=FakeLLMIdentityHallucination())

    result = asyncio.run(orchestrator.handle("Кто ты?", {"user_id": 1}))

    assert result.status == "ok"
    assert result.text.strip() == CANONICAL_IDENTITY_ANSWER.strip()
    assert result.intent == "identity.query"
    assert result.mode == "local"


def test_who_are_you_english_returns_canonical(tmp_path: Path) -> None:
    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(config={}, storage=storage, llm_client=FakeLLMIdentityHallucination())

    result = asyncio.run(orchestrator.handle("Who are you?", {"user_id": 1}))

    assert result.status == "ok"
    assert result.text.strip() == CANONICAL_IDENTITY_ANSWER.strip()
    assert result.intent == "identity.query"


def test_llm_response_with_forbidden_mention_refused(tmp_path: Path) -> None:
    """Если LLM вернул упоминание компании/провайдера — статус refused."""
    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(config={}, storage=storage, llm_client=FakeLLMIdentityHallucination())

    result = asyncio.run(orchestrator.handle("Расскажи о себе подробнее", {"user_id": 1}))

    assert result.status == "refused"
    assert "Не могу ответить" in result.text
    assert result.debug.get("reason") == "forbidden_identity_mention"


def test_search_ambiguous_query_refused(tmp_path: Path) -> None:
    """При слишком коротком/двусмысленном запросе поиск возвращает отказ с просьбой уточнить."""
    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(config={}, storage=storage, llm_client=FakeLLMNormal(), search_client=FakeSearch([]))

    result = asyncio.run(orchestrator.handle("/search а", {"user_id": 1}))

    assert result.status == "refused"
    assert "Уточни" in result.text
    assert result.debug.get("reason") == "ambiguous_query"


def test_search_no_fact_without_sources(tmp_path: Path) -> None:
    """В режиме строгих фактов без источников — отказ (не возвращаем факт)."""
    storage = TaskStorage(tmp_path / "bot.db")
    orchestrator = Orchestrator(
        config={},
        storage=storage,
        llm_client=FakeLLMNormal("Какой-то факт без ссылок."),
        search_client=FakeSearch([]),
    )

    result = asyncio.run(orchestrator.run_fact_answer(1, "тест запрос", facts_only=True, intent="command.search"))

    assert result.status == "refused"
    assert len(result.sources) == 0
    assert "источник" in result.text.lower() or "переформулир" in result.text.lower()


class FakeSearch:
    def __init__(self, sources: list[Source]) -> None:
        self._sources = sources

    async def search(self, query: str, max_results: int = 5) -> list[Source]:
        return self._sources
