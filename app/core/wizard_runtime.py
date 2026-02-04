from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.core.wizard_manager import WizardManager, WizardState, WizardStepHandler


@dataclass(frozen=True)
class WizardView:
    text: str


WizardRenderer = Callable[[WizardState], str]


class WizardRuntime:
    def __init__(self, manager: WizardManager) -> None:
        self._manager = manager
        self._renderers: dict[str, dict[str, WizardRenderer]] = {}

    def register_step(
        self,
        wizard_id: str,
        step_id: str,
        handler: WizardStepHandler,
        renderer: WizardRenderer,
    ) -> None:
        self._manager.register_step(wizard_id, step_id, handler)
        self._renderers.setdefault(wizard_id, {})[step_id] = renderer

    def has_active(self, user_id: int, chat_id: int) -> bool:
        return self._manager.is_active(user_id, chat_id)

    def start(self, user_id: int, chat_id: int, wizard_id: str, start_step_id: str) -> WizardView:
        state = self._manager.start(user_id, chat_id, wizard_id, start_step_id)
        return self._render(state)

    def handle_text(self, user_id: int, chat_id: int, text: str) -> WizardView:
        state = self._manager.handle_text(user_id, chat_id, text)
        return self._render(state)

    def cancel(self, user_id: int, chat_id: int) -> Optional[WizardState]:
        return self._manager.cancel(user_id, chat_id)

    def get_active(self, user_id: int, chat_id: int) -> Optional[WizardState]:
        return self._manager.get_active(user_id, chat_id)

    def _render(self, state: WizardState) -> WizardView:
        renderer = self._renderers.get(state.wizard_id, {}).get(state.step_id)
        if renderer is None:
            raise RuntimeError(f"No renderer for {state.wizard_id}:{state.step_id}")
        return WizardView(text=renderer(state))
