from __future__ import annotations

import asyncio
from types import SimpleNamespace
from datetime import datetime

from app.bot import actions, handlers
from app.core import calendar_store
from app.core.calendar_nlp_ru import event_from_text_ru
from app.core.orchestrator import Orchestrator
from app.infra.draft_store import DraftStore
from app.infra.last_state_store import LastStateStore
from app.infra.rate_limiter import RateLimiter
from app.infra.storage import TaskStorage


class DummyMessage:
    def __init__(self, text: str) -> None:
        self.text = text


class DummyUpdate:
    def __init__(self, text: str) -> None:
        self.effective_user = SimpleNamespace(id=1)
        self.effective_chat = SimpleNamespace(id=10)
        self.message = DummyMessage(text)
        self.callback_query = None
        self.effective_message = self.message


class DummyContext:
    def __init__(self, tmp_path) -> None:
        storage = TaskStorage(tmp_path / "bot.db")
        orchestrator = Orchestrator(config={}, storage=storage)
        self.application = SimpleNamespace(
            bot_data={
                "orchestrator": orchestrator,
                "rate_limiter": RateLimiter(),
                "ui_rate_limiter": RateLimiter(),
                "settings": SimpleNamespace(
                    enable_menu=True,
                    enable_wizards=False,
                    strict_no_pseudo_sources=False,
                    reminders_enabled=False,
                    telegram_message_limit=4000,
                ),
                "action_store": actions.ActionStore(),
                "draft_store": DraftStore(),
                "last_state_store": LastStateStore(),
            }
        )
        self.chat_data: dict[str, object] = {}


def test_calendar_nlp_requires_confirm(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    calendar_store.save_store_atomic({"events": [], "reminders": [], "updated_at": datetime.now().isoformat()})

    captured: dict[str, object] = {}

    async def fake_send_result(update, context, result, reply_markup=None):
        captured["result"] = result

    async def fake_guard_access(update, context, bucket="default"):
        return True

    monkeypatch.setattr(handlers, "send_result", fake_send_result)
    monkeypatch.setattr(handlers, "_guard_access", fake_guard_access)

    update = DummyUpdate("встреча с врачом завтра в 15:00")
    context = DummyContext(tmp_path)

    asyncio.run(handlers.chat(update, context))

    result = captured["result"]
    assert result is not None
    assert [action.id for action in result.actions] == [
        "calendar.create_confirm",
        "calendar.create_edit",
        "calendar.create_cancel",
    ]
    store = calendar_store.load_store()
    assert store.get("events") == []


def test_calendar_nlp_date_without_time() -> None:
    now = datetime(2026, 12, 1, 10, 0, tzinfo=calendar_store.BOT_TZ)
    draft = event_from_text_ru("встреча 10.12", now=now, tz=calendar_store.BOT_TZ)
    assert "time" in draft.missing_fields
    assert draft.start_at is None

    draft = event_from_text_ru("встреча 10.12 в 10", now=now, tz=calendar_store.BOT_TZ)
    assert draft.start_at is not None
    assert draft.start_at.hour == 10

    draft = event_from_text_ru("встреча 10.12 10:30", now=now, tz=calendar_store.BOT_TZ)
    assert draft.start_at is not None
    assert draft.start_at.hour == 10
    assert draft.start_at.minute == 30

    draft = event_from_text_ru("встреча 10.12 10", now=now, tz=calendar_store.BOT_TZ)
    assert "time" in draft.missing_fields
    assert draft.start_at is None


def test_calendar_nlp_range_and_duration() -> None:
    now = datetime(2026, 2, 5, 10, 0, tzinfo=calendar_store.BOT_TZ)
    draft = event_from_text_ru("созвон в понедельник с 10 до 11", now=now, tz=calendar_store.BOT_TZ)
    assert draft.start_at is not None
    assert draft.end_at is not None
    assert draft.start_at.hour == 10
    assert draft.end_at.hour == 11

    draft = event_from_text_ru("тренировка сегодня в 19 на 45 минут", now=now, tz=calendar_store.BOT_TZ)
    assert draft.start_at is not None
    assert draft.end_at is not None
    assert (draft.end_at - draft.start_at).total_seconds() == 45 * 60


def test_calendar_nlp_recurrence_human_render() -> None:
    now = datetime(2026, 3, 1, 8, 0, tzinfo=calendar_store.BOT_TZ)
    draft = event_from_text_ru(
        "каждый будний день в 9 кроме 08.03.2026 стендап",
        now=now,
        tz=calendar_store.BOT_TZ,
    )
    assert draft.recurrence is not None
    rendered = handlers._render_event_draft(draft)
    assert "RRULE" not in rendered
