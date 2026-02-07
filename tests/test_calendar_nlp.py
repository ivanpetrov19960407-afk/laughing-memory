from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.bot import actions, handlers
from app.core import calendar_nlp_ru, calendar_store
from app.core.result import Action
from app.infra.draft_store import DraftStore


def asyncio_run(coro):
    return asyncio.run(coro)


@pytest.fixture
def calendar_env(tmp_path, monkeypatch) -> str:
    path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(path))
    return str(path)


def _build_context(draft_store: DraftStore) -> SimpleNamespace:
    settings = SimpleNamespace(
        enable_menu=True,
        enable_wizards=True,
        strict_no_pseudo_sources=True,
        reminders_enabled=True,
        caldav_url=None,
        caldav_username=None,
        caldav_password=None,
        google_oauth_client_id=None,
        google_oauth_client_secret=None,
        public_base_url=None,
        openai_api_key=None,
        perplexity_api_key=None,
        calendar_backend="local",
        feature_web_search=False,
        db_path="data/bot.db",
        allowlist_path="data/allowlist.json",
        dialog_memory_path="data/dialog_memory.json",
        wizard_store_path="data/wizards",
        google_tokens_path="data/google_tokens.db",
    )
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "settings": settings,
                "draft_store": draft_store,
                "action_store": actions.ActionStore(),
                "orchestrator": SimpleNamespace(config={"system_metadata": {}}, is_facts_only=lambda user_id: False),
            }
        ),
        chat_data={},
    )


def _build_update(text: str) -> SimpleNamespace:
    message = SimpleNamespace(text=text, caption=None, message_id=1)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=1, username="tester"),
        effective_chat=SimpleNamespace(id=10, type="private"),
        message=message,
        effective_message=message,
        callback_query=None,
    )


def test_event_from_text_ru_basic() -> None:
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.BOT_TZ)
    draft = calendar_nlp_ru.event_from_text_ru(
        "встреча с врачом завтра в 15:00",
        now=now,
        tz=calendar_store.BOT_TZ,
    )
    assert draft.title
    assert draft.start_at is not None
    assert draft.end_at == draft.start_at + timedelta(minutes=60)


def test_event_from_text_ru_range() -> None:
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.BOT_TZ)
    draft = calendar_nlp_ru.event_from_text_ru(
        "созвон в понедельник с 10 до 11",
        now=now,
        tz=calendar_store.BOT_TZ,
    )
    assert draft.start_at is not None
    assert draft.end_at is not None
    assert draft.end_at > draft.start_at


def test_event_from_text_ru_duration() -> None:
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.BOT_TZ)
    draft = calendar_nlp_ru.event_from_text_ru(
        "тренировка сегодня в 19 на 45 минут",
        now=now,
        tz=calendar_store.BOT_TZ,
    )
    assert draft.start_at is not None
    assert draft.end_at == draft.start_at + timedelta(minutes=45)


def test_event_from_text_ru_recurrence() -> None:
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.BOT_TZ)
    draft = calendar_nlp_ru.event_from_text_ru(
        "каждый будний день в 9 кроме 08.03.2026 стендап",
        now=now,
        tz=calendar_store.BOT_TZ,
    )
    assert draft.recurrence is not None
    assert "BYDAY=MO,TU,WE,TH,FR" in draft.recurrence.rrule
    assert any(exdate.date().isoformat() == "2026-03-08" for exdate in draft.recurrence.exdates)


def test_calendar_nlp_requires_confirm(monkeypatch, calendar_env) -> None:
    _ = calendar_env
    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        _ = update
        _ = context
        _ = reply_markup
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        _ = update
        _ = context
        _ = bucket
        return True

    draft_store = DraftStore()
    update = _build_update("встреча с врачом завтра в 15:00")
    context = _build_context(draft_store)

    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    asyncio_run(handlers.chat(update, context))

    store = calendar_store.load_store()
    assert not store.get("events")
    result = captured["result"]
    assert result.actions


def test_actions_ui_for_confirm(monkeypatch, calendar_env, caplog) -> None:
    _ = calendar_env
    draft_store = DraftStore()
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.BOT_TZ)
    draft = calendar_nlp_ru.event_from_text_ru(
        "встреча завтра в 15:00",
        now=now,
        tz=calendar_store.BOT_TZ,
    )
    entry = draft_store.create(chat_id=10, user_id=1, draft=draft, status="draft")

    action_store = actions.ActionStore()
    action = Action(
        id="calendar.create_confirm",
        label="Confirm",
        payload={"op": "calendar.create_confirm", "draft_id": entry.draft_id},
    )
    action_id = action_store.store_action(action=action, user_id=1, chat_id=10)

    async def fake_send_result(update, context, result, reply_markup=None):
        _ = update
        _ = context
        _ = result
        _ = reply_markup
        return None

    async def fake_guard_access(update, context, bucket="default"):
        _ = update
        _ = context
        _ = bucket
        return True

    async def fake_answer_callback(query):
        _ = query
        return None

    message = SimpleNamespace(message_id=1, reply_text=lambda *args, **kwargs: None)
    callback_query = SimpleNamespace(data=f"a:{action_id}", answer=lambda *args, **kwargs: None)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=10),
        effective_message=message,
        callback_query=callback_query,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "settings": SimpleNamespace(
                    reminders_enabled=True,
                    caldav_url=None,
                    caldav_username=None,
                    caldav_password=None,
                    google_oauth_client_id=None,
                    google_oauth_client_secret=None,
                    public_base_url=None,
                    openai_api_key=None,
                    perplexity_api_key=None,
                ),
                "action_store": action_store,
                "draft_store": draft_store,
                "orchestrator": SimpleNamespace(config={"system_metadata": {}}, is_facts_only=lambda user_id: False),
            }
        ),
        chat_data={},
    )

    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)
    monkeypatch.setattr(handlers, "_safe_answer_callback", fake_answer_callback)

    caplog.set_level("INFO")
    asyncio_run(handlers.action_callback(update, context))

    messages = [record.message for record in caplog.records if "Orchestrator result:" in record.message]
    assert len(messages) == 1
