"""Tests for Stage 10: search sources management and reminder context."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from app.core import calendar_store
from app.core.orchestrator import Orchestrator
from app.core.search_sources import (
    DEFAULT_SOURCES,
    SearchSource,
    get_enabled_sources,
    parse_sources_from_config,
)
from app.infra.search_sources_store import get_disabled, set_disabled, set_enabled
from app.infra.storage import TaskStorage


def test_parse_sources_from_config_empty() -> None:
    assert parse_sources_from_config({}) == DEFAULT_SOURCES
    assert parse_sources_from_config({"search_sources": []}) == DEFAULT_SOURCES


def test_parse_sources_from_config_custom() -> None:
    config = {
        "search_sources": [
            {"id": "a", "name": "A", "priority": 2},
            {"id": "b", "name": "B", "priority": 1},
        ]
    }
    sources = parse_sources_from_config(config)
    assert len(sources) == 2
    assert sources[0].id == "b" and sources[0].priority == 1
    assert sources[1].id == "a" and sources[1].priority == 2


def test_get_enabled_sources_filters_disabled() -> None:
    sources = [
        SearchSource(id="p", name="P", priority=1),
        SearchSource(id="b", name="B", priority=2),
    ]
    assert len(get_enabled_sources(sources, set())) == 2
    assert len(get_enabled_sources(sources, {"b"})) == 1
    assert get_enabled_sources(sources, {"b"})[0].id == "p"
    assert len(get_enabled_sources(sources, {"p", "b"})) == 0


def test_search_sources_store_enable_disable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEARCH_SOURCES_STORE_PATH", str(tmp_path / "sources.json"))
    user_id = 42

    async def _run() -> None:
        disabled = await get_disabled(user_id)
        assert disabled == set()
        changed = await set_disabled(user_id, "backup")
        assert changed is True
        disabled = await get_disabled(user_id)
        assert disabled == {"backup"}
        changed2 = await set_disabled(user_id, "backup")
        assert changed2 is False
        changed3 = await set_enabled(user_id, "backup")
        assert changed3 is True
        disabled2 = await get_disabled(user_id)
        assert disabled2 == set()
        changed4 = await set_enabled(user_id, "backup")
        assert changed4 is False

    asyncio.run(_run())


def test_reminder_with_llm_context_stored_and_retrieved(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CALENDAR_PATH", str(tmp_path / "calendar.json"))
    now = datetime.now(tz=calendar_store.BOT_TZ)
    reminder = asyncio.run(
        calendar_store.add_reminder(
            trigger_at=now + timedelta(hours=1),
            text="Про статью выше",
            chat_id=10,
            user_id=1,
            llm_context="[user] Найди статью про API\n[assistant] Вот ссылка: example.com",
        )
    )
    assert reminder.llm_context is not None
    assert "example.com" in reminder.llm_context

    loaded = asyncio.run(calendar_store.get_reminder(reminder.id))
    assert loaded is not None
    assert loaded.llm_context == reminder.llm_context


def test_reminder_without_llm_context_has_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CALENDAR_PATH", str(tmp_path / "calendar.json"))
    now = datetime.now(tz=calendar_store.BOT_TZ)
    reminder = asyncio.run(
        calendar_store.add_reminder(
            trigger_at=now + timedelta(hours=1),
            text="Ping",
            chat_id=10,
            user_id=1,
        )
    )
    assert reminder.llm_context is None


def test_run_fact_answer_refused_when_all_sources_disabled(tmp_path) -> None:
    """When search_sources_store returns all sources as disabled, run_fact_answer returns refused."""
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    config = {
        "search_sources": [
            {"id": "p", "name": "P", "priority": 1},
            {"id": "b", "name": "B", "priority": 2},
        ],
    }

    class AllDisabledStore:
        async def get_disabled(self, user_id: int):
            return {"p", "b"}

    orchestrator = Orchestrator(
        config=config,
        storage=storage,
        feature_web_search=True,
        search_sources_store=AllDisabledStore(),
    )
    result = asyncio.run(orchestrator.run_fact_answer(1, "test query", facts_only=True, intent="command.search"))
    assert result.status == "refused"
    assert "нет включённых источников" in result.text.lower() or "search_sources" in result.text.lower()
