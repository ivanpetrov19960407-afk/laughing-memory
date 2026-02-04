from __future__ import annotations

from app.core.wizard_manager import WizardState
from app.core.wizard_runtime import WizardRuntime

WIZARD_ID = "echo_confirm"
STEP_ASK = "ask"
STEP_CONFIRM = "confirm"


def register(runtime: WizardRuntime) -> None:
    def handle_ask(state: WizardState, text: str) -> str | None:
        draft = text.strip()
        if draft:
            state.data["draft"] = draft
            return STEP_CONFIRM
        return None

    def render_ask(state: WizardState) -> str:
        return "Напиши текст для подтверждения."

    def handle_confirm(state: WizardState, text: str) -> str | None:
        return None

    def render_confirm(state: WizardState) -> str:
        draft = state.data.get("draft", "")
        return f"Черновик:\n{draft}\n\nПодтвердить?"

    runtime.register_step(WIZARD_ID, STEP_ASK, handle_ask, render_ask)
    runtime.register_step(WIZARD_ID, STEP_CONFIRM, handle_confirm, render_confirm)
