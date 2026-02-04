from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.wizard_manager import WizardManager


def test_start_unknown_wizard_id() -> None:
    manager = WizardManager()

    with pytest.raises(ValueError):
        manager.start(user_id=1, chat_id=10, wizard_id="unknown", start_step_id="step1")


def test_start_unknown_step_id() -> None:
    manager = WizardManager()
    manager.register_step("wizard", "step1", lambda state, text: None)

    with pytest.raises(ValueError):
        manager.start(user_id=1, chat_id=10, wizard_id="wizard", start_step_id="missing")


def test_handle_text_without_active() -> None:
    manager = WizardManager()

    with pytest.raises(RuntimeError):
        manager.handle_text(user_id=1, chat_id=10, text="hi")


def test_step_transition() -> None:
    manager = WizardManager()

    def step1_handler(state, text) -> str:
        return "step2"

    manager.register_step("wizard", "step1", step1_handler)
    manager.register_step("wizard", "step2", lambda state, text: None)

    state = manager.start(user_id=1, chat_id=10, wizard_id="wizard", start_step_id="step1")
    assert state.step_id == "step1"

    updated = manager.handle_text(user_id=1, chat_id=10, text="next")
    assert updated.step_id == "step2"


def test_unknown_next_step_raises() -> None:
    manager = WizardManager()

    def step1_handler(state, text) -> str:
        return "missing"

    manager.register_step("wizard", "step1", step1_handler)

    manager.start(user_id=1, chat_id=10, wizard_id="wizard", start_step_id="step1")

    with pytest.raises(RuntimeError):
        manager.handle_text(user_id=1, chat_id=10, text="next")


def test_cancel_clears_active_state() -> None:
    manager = WizardManager()
    manager.register_step("wizard", "step1", lambda state, text: None)

    manager.start(user_id=1, chat_id=10, wizard_id="wizard", start_step_id="step1")
    canceled = manager.cancel(user_id=1, chat_id=10)

    assert canceled is not None
    assert manager.get_active(user_id=1, chat_id=10) is None


def test_timeout_detection() -> None:
    manager = WizardManager(timeout_seconds=60)
    manager.register_step("wizard", "step1", lambda state, text: None)
    now = datetime(2026, 2, 5, 12, 0, tzinfo=timezone.utc)
    state = manager.start(user_id=1, chat_id=10, wizard_id="wizard", start_step_id="step1")
    state.last_activity_at = now - timedelta(minutes=2)

    assert manager.is_timed_out(state, now=now) is True
