from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.core import calendar_store
from app.core.result import Action, OrchestratorResult, error, ok, refused
from app.storage.wizard_store import WizardState, WizardStore

LOGGER = logging.getLogger(__name__)

WIZARD_CALENDAR_ADD = "calendar.add_event"
WIZARD_REMINDER_RESCHEDULE = "reminder.reschedule"
STEP_AWAIT_DATETIME = "await_datetime"
STEP_AWAIT_TITLE = "await_title"
STEP_CONFIRM = "confirm"


class WizardManager:
    def __init__(self, store: WizardStore, *, reminder_scheduler=None, settings=None) -> None:
        self._store = store
        self._reminder_scheduler = reminder_scheduler
        self._settings = settings

    def get_state(self, *, user_id: int, chat_id: int) -> tuple[WizardState | None, bool]:
        return self._store.load_state(user_id=user_id, chat_id=chat_id)

    async def handle_text(
        self,
        *,
        user_id: int,
        chat_id: int,
        text: str,
    ) -> OrchestratorResult | None:
        state, expired = self._store.load_state(user_id=user_id, chat_id=chat_id)
        if expired:
            return _expired_result()
        if state is None:
            return None
        if state.wizard_id == WIZARD_CALENDAR_ADD:
            return await self._handle_calendar_add_text(state, user_id=user_id, chat_id=chat_id, text=text)
        if state.wizard_id == WIZARD_REMINDER_RESCHEDULE:
            return await self._handle_reminder_reschedule_text(state, user_id=user_id, chat_id=chat_id, text=text)
        return refused(
            "Неизвестный сценарий. Открой /menu.",
            intent="wizard.unknown",
            mode="local",
            actions=_menu_actions(),
        )

    async def handle_action(
        self,
        *,
        user_id: int,
        chat_id: int,
        op: str,
        payload: dict[str, object],
    ) -> OrchestratorResult | None:
        state, expired = self._store.load_state(user_id=user_id, chat_id=chat_id)
        if expired:
            return _expired_result()
        wizard_id = payload.get("wizard_id")
        if op == "wizard_start":
            if not isinstance(wizard_id, str):
                return refused("Некорректный сценарий.", intent="wizard.start", mode="local")
            return self._start_wizard(
                wizard_id,
                user_id=user_id,
                chat_id=chat_id,
                active_state=state,
            )
        if op in {"wizard_continue", "wizard_restart"}:
            if state is None:
                return refused("Нет активного сценария.", intent="wizard.continue", mode="local")
            if op == "wizard_restart":
                self._store.clear_state(user_id=user_id, chat_id=chat_id)
                return self._start_wizard(state.wizard_id, user_id=user_id, chat_id=chat_id, active_state=None)
            return _render_prompt(state)
        if op == "wizard_cancel":
            if state is None:
                return refused("Активный сценарий не найден.", intent="wizard.cancel", mode="local")
            self._store.clear_state(user_id=user_id, chat_id=chat_id)
            return refused(
                "Сценарий отменён.",
                intent="wizard.cancel",
                mode="local",
                actions=_menu_actions(),
            )
        if state is None:
            return None
        if state.wizard_id == WIZARD_CALENDAR_ADD:
            return await self._handle_calendar_add_action(
                state,
                user_id=user_id,
                chat_id=chat_id,
                op=op,
                payload=payload,
            )
        if state.wizard_id == WIZARD_REMINDER_RESCHEDULE:
            return await self._handle_reminder_reschedule_action(
                state,
                user_id=user_id,
                chat_id=chat_id,
                op=op,
                payload=payload,
            )
        return None

    def cancel(self, *, user_id: int, chat_id: int) -> OrchestratorResult:
        state, expired = self._store.load_state(user_id=user_id, chat_id=chat_id)
        if expired:
            return _expired_result()
        if state is None:
            return refused("Активный сценарий не найден.", intent="wizard.cancel", mode="local")
        self._store.clear_state(user_id=user_id, chat_id=chat_id)
        return refused("Сценарий отменён.", intent="wizard.cancel", mode="local", actions=_menu_actions())

    def _start_wizard(
        self,
        wizard_id: str,
        *,
        user_id: int,
        chat_id: int,
        active_state: WizardState | None,
    ) -> OrchestratorResult:
        if active_state is not None:
            return ok(
                "У тебя уже есть активный сценарий. Продолжить или начать заново?",
                intent="wizard.resume_prompt",
                mode="local",
                actions=_resume_actions(active_state.wizard_id),
            )
        if wizard_id != WIZARD_CALENDAR_ADD:
            return refused("Сценарий недоступен.", intent="wizard.start", mode="local")
        now = datetime.now(timezone.utc)
        state = WizardState(
            wizard_id=wizard_id,
            step=STEP_AWAIT_DATETIME,
            data={},
            started_at=now,
            updated_at=now,
        )
        self._store.save_state(user_id=user_id, chat_id=chat_id, state=state)
        return _render_prompt(state)

    async def start_reminder_reschedule(
        self,
        *,
        user_id: int,
        chat_id: int,
        reminder_id: str,
    ) -> OrchestratorResult:
        state, expired = self._store.load_state(user_id=user_id, chat_id=chat_id)
        if expired:
            return _expired_result()
        if state is not None:
            return ok(
                "У тебя уже есть активный сценарий. Продолжить или начать заново?",
                intent="wizard.resume_prompt",
                mode="local",
                actions=_resume_actions(state.wizard_id),
            )
        reminder = await calendar_store.get_reminder(reminder_id)
        if reminder is None:
            return refused(
                f"Напоминание не найдено: {reminder_id}",
                intent="wizard.reminder.missing",
                mode="local",
            )
        if reminder.status != "active":
            return refused(
                "Напоминание отключено, перенос недоступен.",
                intent="wizard.reminder.disabled",
                mode="local",
            )
        now = datetime.now(timezone.utc)
        state = WizardState(
            wizard_id=WIZARD_REMINDER_RESCHEDULE,
            step=STEP_AWAIT_DATETIME,
            data={
                "reminder_id": reminder_id,
                "old_trigger_at": reminder.trigger_at.isoformat(),
            },
            started_at=now,
            updated_at=now,
        )
        self._store.save_state(user_id=user_id, chat_id=chat_id, state=state)
        return _render_prompt(state)

    async def _handle_calendar_add_text(
        self,
        state: WizardState,
        *,
        user_id: int,
        chat_id: int,
        text: str,
    ) -> OrchestratorResult:
        if state.step == STEP_AWAIT_DATETIME:
            try:
                dt = calendar_store.parse_local_datetime(text)
            except ValueError as exc:
                return refused(
                    f"{exc}. Пример: 2026-02-05 18:30 или 05.02.2026 18:30",
                    intent="wizard.calendar.datetime",
                    mode="local",
                    actions=_step_actions(),
                )
            updated = _touch_state(state, step=STEP_AWAIT_TITLE, data={"dt": dt.isoformat()})
            self._store.save_state(user_id=user_id, chat_id=chat_id, state=updated)
            return ok(
                "Отлично. Теперь напиши название события.",
                intent="wizard.calendar.title",
                mode="local",
                actions=_step_actions(),
            )
        if state.step == STEP_AWAIT_TITLE:
            title = text.strip()
            if not title:
                return refused(
                    "Название не может быть пустым. Попробуй ещё раз.",
                    intent="wizard.calendar.title",
                    mode="local",
                    actions=_step_actions(),
                )
            updated = _touch_state(state, step=STEP_CONFIRM, data={"title": title})
            self._store.save_state(user_id=user_id, chat_id=chat_id, state=updated)
            return _render_prompt(updated)
        if state.step == STEP_CONFIRM:
            return refused(
                "Подтверди действие кнопками ниже.",
                intent="wizard.calendar.confirm",
                mode="local",
                actions=_confirm_actions(),
            )
        return refused("Шаг сценария не распознан.", intent="wizard.calendar.step", mode="local")

    async def _handle_calendar_add_action(
        self,
        state: WizardState,
        *,
        user_id: int,
        chat_id: int,
        op: str,
        payload: dict[str, object],
    ) -> OrchestratorResult:
        if op == "wizard_edit":
            updated = _touch_state(state, step=STEP_AWAIT_DATETIME, data={})
            self._store.save_state(user_id=user_id, chat_id=chat_id, state=updated)
            return _render_prompt(updated)
        if op != "wizard_confirm":
            return refused("Действие не поддерживается.", intent="wizard.calendar.action", mode="local")
        if state.step != STEP_CONFIRM:
            return refused("Сначала заполни данные.", intent="wizard.calendar.confirm", mode="local")
        dt_value = state.data.get("dt")
        title = state.data.get("title")
        if not isinstance(dt_value, str) or not isinstance(title, str) or not title.strip():
            return refused("Не хватает данных для создания события.", intent="wizard.calendar.confirm", mode="local")
        try:
            dt = datetime.fromisoformat(dt_value)
        except ValueError:
            return refused("Дата повреждена, начни заново.", intent="wizard.calendar.confirm", mode="local")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=calendar_store.VIENNA_TZ)
        try:
            created = await calendar_store.add_item(
                dt=dt,
                title=title.strip(),
                chat_id=chat_id,
                remind_at=None,
                user_id=user_id,
                reminders_enabled=False,
            )
        except Exception as exc:
            LOGGER.exception("Failed to create calendar item: %s", exc)
            return error(
                "Не удалось создать событие. Попробуй позже.",
                intent="wizard.calendar.confirm",
                mode="local",
            )
        self._store.clear_state(user_id=user_id, chat_id=chat_id)
        event = created.get("event") if isinstance(created, dict) else None
        event_id = event.get("event_id") if isinstance(event, dict) else None
        display_dt = dt.astimezone(calendar_store.VIENNA_TZ).strftime("%Y-%m-%d %H:%M")
        actions = _post_create_actions(event_id if isinstance(event_id, str) else "")
        return ok(
            f"Готово! Событие добавлено: {display_dt} — {title.strip()}",
            intent="wizard.calendar.done",
            mode="local",
            actions=actions,
        )

    async def _handle_reminder_reschedule_text(
        self,
        state: WizardState,
        *,
        user_id: int,
        chat_id: int,
        text: str,
    ) -> OrchestratorResult:
        if state.step != STEP_AWAIT_DATETIME:
            return refused(
                "Подтверди действие кнопками ниже.",
                intent="wizard.reminder.confirm",
                mode="local",
                actions=_confirm_actions(),
            )
        try:
            dt = calendar_store.parse_user_datetime(text)
        except ValueError as exc:
            return refused(
                f"{exc}. Пример: 2026-02-05 18:30 или 05.02.2026 18:30",
                intent="wizard.reminder.datetime",
                mode="local",
                actions=_step_actions(),
            )
        updated = _touch_state(state, step=STEP_CONFIRM, data={"new_trigger_at": dt.isoformat()})
        self._store.save_state(user_id=user_id, chat_id=chat_id, state=updated)
        return _render_prompt(updated)

    async def _handle_reminder_reschedule_action(
        self,
        state: WizardState,
        *,
        user_id: int,
        chat_id: int,
        op: str,
        payload: dict[str, object],
    ) -> OrchestratorResult:
        if op == "wizard_edit":
            updated = _touch_state(state, step=STEP_AWAIT_DATETIME, data={"new_trigger_at": None})
            self._store.save_state(user_id=user_id, chat_id=chat_id, state=updated)
            return _render_prompt(updated)
        if op != "wizard_confirm":
            return refused("Действие не поддерживается.", intent="wizard.reminder.action", mode="local")
        if state.step != STEP_CONFIRM:
            return refused("Сначала заполни данные.", intent="wizard.reminder.confirm", mode="local")
        reminder_id = state.data.get("reminder_id")
        new_trigger_value = state.data.get("new_trigger_at")
        if not isinstance(reminder_id, str) or not isinstance(new_trigger_value, str):
            return refused("Не хватает данных для переноса.", intent="wizard.reminder.confirm", mode="local")
        try:
            new_trigger = datetime.fromisoformat(new_trigger_value)
        except ValueError:
            return refused("Дата повреждена, начни заново.", intent="wizard.reminder.confirm", mode="local")
        if new_trigger.tzinfo is None:
            new_trigger = new_trigger.replace(tzinfo=calendar_store.VIENNA_TZ)
        reminder = await calendar_store.get_reminder(reminder_id)
        if reminder is None:
            return refused(
                f"Напоминание не найдено: {reminder_id}",
                intent="wizard.reminder.missing",
                mode="local",
            )
        updated = await calendar_store.update_reminder_trigger(reminder_id, new_trigger, enabled=True)
        if updated is None:
            return error(
                "Не удалось перенести напоминание.",
                intent="wizard.reminder.confirm",
                mode="local",
            )
        if self._reminder_scheduler and (self._settings is None or self._settings.reminders_enabled):
            try:
                await self._reminder_scheduler.schedule_reminder(updated)
            except Exception:
                LOGGER.exception("Failed to reschedule reminder: reminder_id=%s", reminder_id)
                return error(
                    "Не удалось перенести напоминание.",
                    intent="wizard.reminder.confirm",
                    mode="local",
                )
        self._store.clear_state(user_id=user_id, chat_id=chat_id)
        LOGGER.info(
            "Reminder rescheduled: reminder_id=%s user_id=%s old_trigger_at=%s new_trigger_at=%s",
            reminder_id,
            reminder.user_id,
            reminder.trigger_at.isoformat(),
            updated.trigger_at.isoformat(),
        )
        display_dt = updated.trigger_at.astimezone(calendar_store.VIENNA_TZ).strftime("%Y-%m-%d %H:%M")
        return ok(
            f"Ок, перенёс на {display_dt}.",
            intent="wizard.reminder.done",
            mode="local",
            actions=_menu_actions(),
        )


def _touch_state(state: WizardState, *, step: str | None = None, data: dict[str, object] | None = None) -> WizardState:
    updated = datetime.now(timezone.utc)
    merged_data = dict(state.data)
    if data:
        merged_data.update(data)
    return replace(
        state,
        step=step or state.step,
        data=merged_data,
        updated_at=updated,
    )


def _render_prompt(state: WizardState) -> OrchestratorResult:
    if state.wizard_id == WIZARD_REMINDER_RESCHEDULE and state.step == STEP_AWAIT_DATETIME:
        return ok(
            "Пришли новую дату и время в формате YYYY-MM-DD HH:MM или DD.MM.YYYY HH:MM.\n"
            "Можно: сегодня 18:30 или завтра 09:00.",
            intent="wizard.reminder.datetime",
            mode="local",
            actions=_step_actions(),
        )
    if state.wizard_id == WIZARD_REMINDER_RESCHEDULE and state.step == STEP_CONFIRM:
        new_trigger_value = state.data.get("new_trigger_at")
        if isinstance(new_trigger_value, str):
            try:
                new_trigger = datetime.fromisoformat(new_trigger_value)
            except ValueError:
                new_trigger = None
        else:
            new_trigger = None
        display_dt = (
            new_trigger.astimezone(calendar_store.VIENNA_TZ).strftime("%Y-%m-%d %H:%M")
            if isinstance(new_trigger, datetime)
            else "неизвестно"
        )
        return ok(
            f"Перенести напоминание на {display_dt}?",
            intent="wizard.reminder.confirm",
            mode="local",
            actions=_confirm_actions(),
        )
    if state.step == STEP_AWAIT_DATETIME:
        return ok(
            "Введи дату и время события в формате YYYY-MM-DD HH:MM или DD.MM.YYYY HH:MM.\n"
            "Пример: 2026-02-05 18:30 или 05.02.2026 18:30",
            intent="wizard.calendar.datetime",
            mode="local",
            actions=_step_actions(),
        )
    if state.step == STEP_AWAIT_TITLE:
        return ok(
            "Как назвать событие? Например: Врач",
            intent="wizard.calendar.title",
            mode="local",
            actions=_step_actions(),
        )
    if state.step == STEP_CONFIRM:
        dt_value = state.data.get("dt")
        title = state.data.get("title")
        if isinstance(dt_value, str):
            try:
                dt = datetime.fromisoformat(dt_value)
            except ValueError:
                dt = None
        else:
            dt = None
        display_dt = (
            dt.astimezone(calendar_store.VIENNA_TZ).strftime("%Y-%m-%d %H:%M")
            if isinstance(dt, datetime)
            else "неизвестно"
        )
        title_text = title if isinstance(title, str) and title.strip() else "без названия"
        return ok(
            f"Верно понял: {display_dt} — {title_text}. Создать?",
            intent="wizard.calendar.confirm",
            mode="local",
            actions=_confirm_actions(),
        )
    return refused("Неизвестный шаг.", intent="wizard.calendar.step", mode="local")


def _expired_result() -> OrchestratorResult:
    return refused(
        "Сценарий истёк, начни заново.",
        intent="wizard.expired",
        mode="local",
        actions=_menu_actions(),
    )


def _menu_actions() -> list[Action]:
    return [Action(id="menu.open", label="🏠 Меню", payload={"op": "menu_open"})]


def _step_actions() -> list[Action]:
    return [
        Action(id="wizard.cancel", label="❌ Отмена", payload={"op": "wizard_cancel"}),
        Action(id="menu.open", label="🏠 Меню", payload={"op": "menu_open"}),
    ]


def _confirm_actions() -> list[Action]:
    return [
        Action(id="wizard.confirm", label="✅ Да", payload={"op": "wizard_confirm"}),
        Action(id="wizard.edit", label="✏️ Изменить", payload={"op": "wizard_edit"}),
        Action(id="wizard.cancel", label="❌ Отмена", payload={"op": "wizard_cancel"}),
    ]


def _resume_actions(wizard_id: str) -> list[Action]:
    return [
        Action(id="wizard.continue", label="▶️ Продолжить", payload={"op": "wizard_continue", "wizard_id": wizard_id}),
        Action(id="wizard.restart", label="🔄 Начать заново", payload={"op": "wizard_restart", "wizard_id": wizard_id}),
        Action(id="wizard.cancel", label="❌ Отмена", payload={"op": "wizard_cancel", "wizard_id": wizard_id}),
    ]


def _post_create_actions(event_id: str) -> list[Action]:
    actions = [
        Action(
            id="reminder.add_10m",
            label="➕ Добавить напоминание (10 мин до)",
            payload={"op": "reminder_add_offset", "event_id": event_id, "minutes": 10},
        ),
        Action(
            id="reminders.list",
            label="📋 Показать ближайшие",
            payload={"op": "reminders_list", "limit": 5},
        ),
        Action(id="menu.open", label="🏠 Меню", payload={"op": "menu_open"}),
    ]
    return actions
