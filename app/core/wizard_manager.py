from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional


@dataclass
class WizardState:
    wizard_id: str
    step_id: str
    data: dict
    started_at: datetime
    last_activity_at: datetime

    def touch(self) -> None:
        self.last_activity_at = datetime.now(timezone.utc)


WizardStepHandler = Callable[[WizardState, str], Optional[str]]


class WizardManager:
    def __init__(self, timeout_seconds: int = 900) -> None:
        self._timeout_seconds = timeout_seconds
        self._registry: dict[str, dict[str, WizardStepHandler]] = {}
        self._active: dict[tuple[int, int], WizardState] = {}

    def register_step(self, wizard_id: str, step_id: str, handler: WizardStepHandler) -> None:
        self._registry.setdefault(wizard_id, {})[step_id] = handler

    def has_wizard(self, wizard_id: str) -> bool:
        return wizard_id in self._registry

    def get_active(self, user_id: int, chat_id: int) -> Optional[WizardState]:
        return self._active.get((user_id, chat_id))

    def is_active(self, user_id: int, chat_id: int) -> bool:
        return (user_id, chat_id) in self._active

    def start(self, user_id: int, chat_id: int, wizard_id: str, start_step_id: str) -> WizardState:
        if wizard_id not in self._registry:
            raise ValueError(f"Unknown wizard_id: {wizard_id}")
        if start_step_id not in self._registry[wizard_id]:
            raise ValueError(f"Unknown step_id for wizard {wizard_id}: {start_step_id}")
        now = datetime.now(timezone.utc)
        state = WizardState(
            wizard_id=wizard_id,
            step_id=start_step_id,
            data={},
            started_at=now,
            last_activity_at=now,
        )
        self._active[(user_id, chat_id)] = state
        return state

    def cancel(self, user_id: int, chat_id: int) -> Optional[WizardState]:
        return self._active.pop((user_id, chat_id), None)

    def handle_text(self, user_id: int, chat_id: int, text: str) -> WizardState:
        state = self._active.get((user_id, chat_id))
        if state is None:
            raise RuntimeError("No active wizard")
        handler = self._registry.get(state.wizard_id, {}).get(state.step_id)
        if handler is None:
            raise RuntimeError(f"Unknown step_id for wizard {state.wizard_id}: {state.step_id}")
        next_step = handler(state, text)
        if next_step is not None:
            if next_step not in self._registry[state.wizard_id]:
                raise RuntimeError(f"Unknown next_step for wizard {state.wizard_id}: {next_step}")
            state.step_id = next_step
        state.touch()
        return state

    def is_timed_out(self, state: WizardState, now: Optional[datetime] = None) -> bool:
        if now is None:
            now = datetime.now(timezone.utc)
        return now - state.last_activity_at > timedelta(seconds=self._timeout_seconds)
