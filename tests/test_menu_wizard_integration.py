"""Integration tests: menu sections, wizard flow without LLM, calendar add via menu."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.bot.wizard import (
    WizardManager,
    WIZARD_CALENDAR_ADD,
    WIZARD_REMINDER_CREATE,
)
from app.bot.menu import build_menu_actions
from app.bot import handlers
from app.core import calendar_store
from app.core.result import Action
from app.storage.wizard_store import WizardStore, WizardState


# ---- Menu build ----

def test_menu_actions_contains_all_sections() -> None:
    actions = build_menu_actions(facts_enabled=False, enable_menu=True)
    labels = {a.label for a in actions}
    assert "💬 Чат" in labels
    assert "🔎 Поиск" in labels
    assert "🖼 Картинки" in labels
    assert "🧮 Калькулятор" in labels
    assert "📅 Календарь" in labels
    assert "⏰ Напоминания" in labels
    assert "⚙️ Настройки" in labels


def test_menu_section_handler_returns_result() -> None:
    result = asyncio.run(
        handlers._handle_menu_section(
            None,
            section="home",
            user_id=1,
            chat_id=10,
        )
    )
    assert result.status == "ok"
    assert result.actions


def test_menu_section_unknown_returns_refused() -> None:
    result = asyncio.run(
        handlers._handle_menu_section(
            None,
            section="nonexistent",
            user_id=1,
            chat_id=10,
        )
    )
    assert result.status == "refused"


# ---- Wizard: calendar add full flow without LLM ----

def test_calendar_add_wizard_full_flow(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    store = WizardStore(tmp_path / "wizards", timeout_seconds=600)
    manager = WizardManager(store)

    # Start wizard
    result = asyncio.run(
        manager.handle_action(
            user_id=1,
            chat_id=10,
            op="wizard_start",
            payload={"wizard_id": WIZARD_CALENDAR_ADD},
        )
    )
    assert result is not None
    assert result.status == "ok"

    # Enter date
    result = asyncio.run(manager.handle_text(user_id=1, chat_id=10, text="2026-03-15 14:00"))
    assert result is not None
    assert result.status == "ok"
    assert "название" in result.text.lower() or "назвать" in result.text.lower()

    # Enter title
    result = asyncio.run(manager.handle_text(user_id=1, chat_id=10, text="Встреча"))
    assert result is not None
    assert result.status == "ok"
    assert any(a.id == "wizard.confirm" for a in result.actions)

    # Confirm
    result = asyncio.run(
        manager.handle_action(
            user_id=1,
            chat_id=10,
            op="wizard_confirm",
            payload={},
        )
    )
    assert result is not None
    assert result.status == "ok"
    assert "добавлено" in result.text.lower() or "готово" in result.text.lower()

    # Check event created
    items = asyncio.run(calendar_store.list_items(None, None))
    assert any(item.title == "Встреча" for item in items)


# ---- Wizard: cancel flow ----

def test_wizard_cancel_clears_state(tmp_path) -> None:
    store = WizardStore(tmp_path / "wizards", timeout_seconds=600)
    manager = WizardManager(store)

    asyncio.run(
        manager.handle_action(
            user_id=2,
            chat_id=20,
            op="wizard_start",
            payload={"wizard_id": WIZARD_CALENDAR_ADD},
        )
    )

    state, _ = store.load_state(user_id=2, chat_id=20)
    assert state is not None

    result = manager.cancel(user_id=2, chat_id=20)
    assert result.status == "refused"
    assert "отмен" in result.text.lower()

    state, _ = store.load_state(user_id=2, chat_id=20)
    assert state is None


# ---- Wizard: timeout detection ----

def test_wizard_timeout_returns_expired(tmp_path) -> None:
    store = WizardStore(tmp_path / "wizards", timeout_seconds=60)
    manager = WizardManager(store)

    now = datetime.now(timezone.utc)
    expired_state = WizardState(
        wizard_id=WIZARD_CALENDAR_ADD,
        step="await_datetime",
        data={},
        started_at=now - timedelta(minutes=5),
        updated_at=now - timedelta(minutes=3),
    )
    store.save_state(user_id=3, chat_id=30, state=expired_state)

    result = asyncio.run(manager.handle_text(user_id=3, chat_id=30, text="2026-02-05 18:30"))
    assert result is not None
    assert "истёк" in result.text.lower()


# ---- Wizard: re-entry when active ----

def test_wizard_re_entry_when_active(tmp_path) -> None:
    store = WizardStore(tmp_path / "wizards", timeout_seconds=600)
    manager = WizardManager(store)

    asyncio.run(
        manager.handle_action(
            user_id=4,
            chat_id=40,
            op="wizard_start",
            payload={"wizard_id": WIZARD_CALENDAR_ADD},
        )
    )

    result = asyncio.run(
        manager.handle_action(
            user_id=4,
            chat_id=40,
            op="wizard_start",
            payload={"wizard_id": WIZARD_CALENDAR_ADD},
        )
    )
    assert result is not None
    assert "активный" in result.text.lower() or "продолжить" in result.text.lower()
    assert any(a.label.startswith("▶") for a in result.actions)


# ---- Wizard: reminder create flow ----

def test_reminder_create_wizard_flow(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    store = WizardStore(tmp_path / "wizards", timeout_seconds=600)
    manager = WizardManager(store)

    result = asyncio.run(
        manager.handle_action(
            user_id=5,
            chat_id=50,
            op="wizard_start",
            payload={"wizard_id": WIZARD_REMINDER_CREATE},
        )
    )
    assert result is not None
    assert result.status == "ok"

    # Enter text
    result = asyncio.run(manager.handle_text(user_id=5, chat_id=50, text="Купить молоко"))
    assert result is not None
    assert result.status == "ok"

    # Enter datetime
    result = asyncio.run(manager.handle_text(user_id=5, chat_id=50, text="2026-03-15 09:00"))
    assert result is not None
    assert result.status == "ok"

    # Enter recurrence
    result = asyncio.run(manager.handle_text(user_id=5, chat_id=50, text="none"))
    assert result is not None
    assert "создать" in result.text.lower() or "поставил" in result.text.lower() or "напоминание" in result.text.lower()

    # Confirm
    result = asyncio.run(
        manager.handle_action(
            user_id=5,
            chat_id=50,
            op="wizard_confirm",
            payload={},
        )
    )
    assert result is not None
    assert result.status == "ok"
