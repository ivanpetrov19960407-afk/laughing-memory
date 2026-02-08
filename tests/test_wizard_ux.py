from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.bot import wizard
from app.bot.wizard import WizardManager
from app.storage.wizard_store import WizardState, WizardStore


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def test_cancel_exits_wizard_and_shows_menu(tmp_path) -> None:
    store = WizardStore(tmp_path / "wizards")
    manager = WizardManager(store)
    asyncio_run(
        manager.handle_action(
            user_id=1,
            chat_id=10,
            op="wizard_start",
            payload={"wizard_id": wizard.WIZARD_CALENDAR_ADD},
        )
    )

    cancelled = manager.cancel(user_id=1, chat_id=10)
    assert cancelled.status == "refused"
    assert any(action.label == "🏠 Меню" for action in cancelled.actions)

    state, expired = store.load_state(user_id=1, chat_id=10)
    assert state is None
    assert expired is False

    follow_up = asyncio_run(manager.handle_text(user_id=1, chat_id=10, text="2026-02-05 18:30"))
    assert follow_up is None


def test_wizard_timeout_shows_menu(tmp_path) -> None:
    store = WizardStore(tmp_path / "wizards", timeout_seconds=60)
    manager = WizardManager(store)
    now = datetime.now(timezone.utc)
    expired_state = WizardState(
        wizard_id=wizard.WIZARD_CALENDAR_ADD,
        step=wizard.STEP_AWAIT_DATETIME,
        data={},
        started_at=now - timedelta(minutes=5),
        updated_at=now - timedelta(minutes=3),
    )
    store.save_state(user_id=2, chat_id=20, state=expired_state)

    result = asyncio_run(manager.handle_text(user_id=2, chat_id=20, text="2026-02-05 18:30"))
    assert result is not None
    assert "истёк" in result.text.lower()
    assert any(action.label == "🏠 Меню" for action in result.actions)

    state, expired = store.load_state(user_id=2, chat_id=20)
    assert state is None
    assert expired is False


def test_invalid_input_in_wizard_shows_menu(tmp_path) -> None:
    store = WizardStore(tmp_path / "wizards")
    manager = WizardManager(store)
    asyncio_run(
        manager.handle_action(
            user_id=3,
            chat_id=30,
            op="wizard_start",
            payload={"wizard_id": wizard.WIZARD_CALENDAR_ADD},
        )
    )

    refused = asyncio_run(manager.handle_text(user_id=3, chat_id=30, text="непонятный ввод"))
    assert refused is not None
    assert refused.status == "refused"
    assert "не понял" in refused.text.lower()
    assert any(action.label == "🏠 Меню" for action in refused.actions)

    state, expired = store.load_state(user_id=3, chat_id=30)
    assert state is not None
    assert expired is False
    assert state.step == wizard.STEP_AWAIT_DATETIME

    accepted = asyncio_run(manager.handle_text(user_id=3, chat_id=30, text="2026-02-05 18:30"))
    assert accepted is not None
    assert accepted.status == "ok"

    state, expired = store.load_state(user_id=3, chat_id=30)
    assert state is not None
    assert expired is False
    assert state.step == wizard.STEP_AWAIT_TITLE
