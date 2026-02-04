from __future__ import annotations

from app.core.wizard_manager import WizardState
from app.core.wizard_runtime import WizardRuntime

WIZARD_ID = "echo"
STEP_ASK = "ask"


def register(runtime: WizardRuntime) -> None:
    def handle(state: WizardState, text: str) -> str | None:
        state.data["last_text"] = text
        return None

    def render(state: WizardState) -> str:
        last_text = state.data.get("last_text", "")
        return f"echo: {last_text}\n\nНапиши /cancel_wizard чтобы отменить."

    runtime.register_step(WIZARD_ID, STEP_ASK, handle, render)
