from __future__ import annotations

from datetime import datetime

from app.core import calendar_store
from app.core.wizard_manager import WizardState
from app.core.wizard_runtime import WizardRuntime

WIZARD_ID = "calendar_add"
STEP_ASK = "ask"
STEP_CONFIRM = "confirm"


def register(runtime: WizardRuntime) -> None:
    def handle_ask(state: WizardState, text: str) -> str | None:
        title = text.strip()
        if not title:
            return None
        today = datetime.now(tz=calendar_store.BOT_TZ).date()
        state.data["title"] = title
        state.data["date"] = today.isoformat()
        return STEP_CONFIRM

    def render_ask(state: WizardState) -> str:
        return "Введите название события."

    def handle_confirm(state: WizardState, text: str) -> str | None:
        return None

    def render_confirm(state: WizardState) -> str:
        title = state.data.get("title", "")
        date_value = state.data.get("date", "")
        return f"Добавить событие?\nНазвание: {title}\nДата: {date_value}"

    runtime.register_step(WIZARD_ID, STEP_ASK, handle_ask, render_ask)
    runtime.register_step(WIZARD_ID, STEP_CONFIRM, handle_confirm, render_confirm)
