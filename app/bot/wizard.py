from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.core import calendar_store
from app.core.result import Action, OrchestratorResult, error, ok, refused
from app.core.tools_calendar import create_event
from app.storage.wizard_store import WizardState, WizardStore

LOGGER = logging.getLogger(__name__)

WIZARD_CALENDAR_ADD = "calendar.add_event"
WIZARD_REMINDER_CREATE = "reminder.create"
WIZARD_REMINDER_RESCHEDULE = "reminder.reschedule"
STEP_AWAIT_DATETIME = "await_datetime"
STEP_AWAIT_TITLE = "await_title"
STEP_AWAIT_RECURRENCE = "await_recurrence"
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
        if state.wizard_id == WIZARD_REMINDER_CREATE:
            return await self._handle_reminder_create_text(state, user_id=user_id, chat_id=chat_id, text=text)
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
            if wizard_id == WIZARD_REMINDER_RESCHEDULE:
                reminder_id = payload.get("reminder_id")
                if not isinstance(reminder_id, str) or not reminder_id:
                    return refused("Некорректный reminder_id.", intent="wizard.start", mode="local")
                return await self.start_reminder_reschedule(user_id=user_id, chat_id=chat_id, reminder_id=reminder_id)
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
                resume_target = payload.get("resume_target")
                target = resume_target if isinstance(resume_target, str) and resume_target else state.wizard_id
                return self._start_wizard(target, user_id=user_id, chat_id=chat_id, active_state=None)
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
        if state.wizard_id == WIZARD_REMINDER_CREATE:
            return await self._handle_reminder_create_action(
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
                actions=_resume_actions(active_state.wizard_id, resume_target=wizard_id),
            )
        if wizard_id not in {WIZARD_CALENDAR_ADD, WIZARD_REMINDER_CREATE}:
            return refused("Сценарий недоступен.", intent="wizard.start", mode="local")
        now = datetime.now(timezone.utc)
        step = STEP_AWAIT_DATETIME
        if wizard_id == WIZARD_REMINDER_CREATE:
            step = STEP_AWAIT_TITLE
        state = WizardState(
            wizard_id=wizard_id,
            step=step,
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
                parsed = calendar_store.parse_calendar_event_from_text(text)
            except ValueError as exc:
                return refused(
                    f"{exc}. Пример: 2026-02-05 18:30, сегодня 18:30, через 10 минут, "
                    "завтра в 7 вечера созвон или в пятницу 10:15 встреча",
                    intent="wizard.calendar.datetime",
                    mode="local",
                    actions=_step_actions(),
                )
            dt = parsed.start_at
            title = parsed.title.strip()
            if title:
                updated = _touch_state(state, step=STEP_CONFIRM, data={"dt": dt.isoformat(), "title": title})
                self._store.save_state(user_id=user_id, chat_id=chat_id, state=updated)
                return _render_prompt(updated)
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
            dt = dt.replace(tzinfo=calendar_store.BOT_TZ)
        reminders_enabled = self._settings is None or bool(getattr(self._settings, "reminders_enabled", False))
        tool_result = await create_event(
            start_at=dt,
            title=title.strip(),
            chat_id=chat_id,
            user_id=user_id,
            request_id=None,
            intent="utility_calendar.add",
            reminder_scheduler=self._reminder_scheduler,
            reminders_enabled=reminders_enabled,
        )
        if tool_result.status != "ok":
            return replace(
                tool_result,
                mode="local",
                intent="wizard.calendar.confirm",
                actions=_confirm_actions(),
            )
        self._store.clear_state(user_id=user_id, chat_id=chat_id)
        event_id = tool_result.debug.get("event_id") if isinstance(tool_result.debug, dict) else None
        actions = _post_create_actions(event_id if isinstance(event_id, str) else "")
        return ok(
            tool_result.text,
            intent="utility_calendar.add",
            mode="local",
            actions=actions,
            debug={"refs": {"event_id": event_id}} if isinstance(event_id, str) else {},
        )


    async def _handle_reminder_create_text(
        self,
        state: WizardState,
        *,
        user_id: int,
        chat_id: int,
        text: str,
    ) -> OrchestratorResult:
        if state.step == STEP_AWAIT_TITLE:
            title = text.strip()
            if not title:
                return refused(
                    "Текст напоминания не должен быть пустым.",
                    intent="wizard.reminder_create.title",
                    mode="local",
                    actions=_step_actions(),
                )
            updated = _touch_state(state, step=STEP_AWAIT_DATETIME, data={"title": title})
            self._store.save_state(user_id=user_id, chat_id=chat_id, state=updated)
            return ok(
                "Когда напомнить? Формат: YYYY-MM-DD HH:MM или DD.MM.YYYY HH:MM.\n"
                "Можно: сегодня 18:30, 07.02 12:00 или через 10 минут.",
                intent="wizard.reminder_create.datetime",
                mode="local",
                actions=_step_actions(),
            )
        if state.step == STEP_AWAIT_DATETIME:
            try:
                dt = calendar_store.parse_user_datetime(text)
            except ValueError as exc:
                return refused(
                    f"{exc}. Пример: сегодня 18:30, 07.02 12:00 или через 10 минут",
                    intent="wizard.reminder_create.datetime",
                    mode="local",
                    actions=_step_actions(),
                )
            updated = _touch_state(state, step=STEP_AWAIT_RECURRENCE, data={"trigger_at": dt.isoformat()})
            self._store.save_state(user_id=user_id, chat_id=chat_id, state=updated)
            return ok(
                "Нужен повтор? Напиши: none/daily/weekdays/weekly:1,3,5/monthly:15.\n"
                "Можно указать интервал: daily/2, weekdays/2, weekly:1,3/2, monthly:15/2.",
                intent="wizard.reminder_create.recurrence",
                mode="local",
                actions=_step_actions(),
            )
        if state.step == STEP_AWAIT_RECURRENCE:
            try:
                recurrence = _parse_recurrence_input(text)
            except ValueError as exc:
                return refused(
                    f"{exc}. Пример: daily, weekdays/2, weekly:1,3/2, monthly:15.",
                    intent="wizard.reminder_create.recurrence",
                    mode="local",
                    actions=_step_actions(),
                )
            updated = _touch_state(state, step=STEP_CONFIRM, data={"recurrence": recurrence})
            self._store.save_state(user_id=user_id, chat_id=chat_id, state=updated)
            return _render_prompt(updated)
        if state.step == STEP_CONFIRM:
            return refused("Подтверди действие кнопками ниже.", intent="wizard.reminder_create.confirm", mode="local", actions=_confirm_actions())
        return refused("Шаг сценария не распознан.", intent="wizard.reminder_create.step", mode="local", actions=_step_actions())

    async def _handle_reminder_create_action(
        self,
        state: WizardState,
        *,
        user_id: int,
        chat_id: int,
        op: str,
        payload: dict[str, object],
    ) -> OrchestratorResult:
        if op == "wizard_edit":
            updated = _touch_state(state, step=STEP_AWAIT_TITLE, data={})
            self._store.save_state(user_id=user_id, chat_id=chat_id, state=updated)
            return _render_prompt(updated)
        if op != "wizard_confirm":
            return refused("Действие не поддерживается.", intent="wizard.reminder_create.action", mode="local")
        if state.step != STEP_CONFIRM:
            return refused("Сначала заполни данные.", intent="wizard.reminder_create.confirm", mode="local")
        title = state.data.get("title")
        trigger_value = state.data.get("trigger_at")
        recurrence_value = state.data.get("recurrence")
        if not isinstance(title, str) or not isinstance(trigger_value, str):
            return refused("Не хватает данных для создания напоминания.", intent="wizard.reminder_create.confirm", mode="local")
        try:
            trigger_at = datetime.fromisoformat(trigger_value)
        except ValueError:
            return refused("Дата повреждена, начни заново.", intent="wizard.reminder_create.confirm", mode="local")
        if trigger_at.tzinfo is None:
            trigger_at = trigger_at.replace(tzinfo=calendar_store.BOT_TZ)
        recurrence = recurrence_value if isinstance(recurrence_value, dict) else None
        try:
            reminder = await calendar_store.add_reminder(
                trigger_at=trigger_at,
                text=title.strip(),
                chat_id=chat_id,
                user_id=user_id,
                recurrence=recurrence,
                enabled=True,
            )
        except Exception:
            LOGGER.exception("Failed to create reminder")
            return error("Не удалось создать напоминание.", intent="wizard.reminder_create.confirm", mode="local")
        if self._reminder_scheduler and (self._settings is None or self._settings.reminders_enabled):
            await self._reminder_scheduler.schedule_reminder(reminder)
        self._store.clear_state(user_id=user_id, chat_id=chat_id)
        display_dt = reminder.trigger_at.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
        LOGGER.info("Reminder created: reminder_id=%s user_id=%s trigger_at=%s", reminder.id, user_id, reminder.trigger_at.isoformat())
        return ok(
            f"Ок, поставил на {display_dt} (МСК).",
            intent="utility_reminders.create",
            mode="local",
            actions=[
                Action(id="utility_reminders.list", label="📋 Список", payload={"op": "reminder.list"}),
                Action(id="menu.open", label="🏠 Меню", payload={"op": "menu_open"}),
            ],
            debug={"refs": {"reminder_id": reminder.id}},
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
                f"{exc}. Пример: сегодня 18:30, 07.02 12:00 или через 10 минут",
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
            new_trigger = new_trigger.replace(tzinfo=calendar_store.BOT_TZ)
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
        display_dt = updated.trigger_at.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
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
    if state.wizard_id == WIZARD_REMINDER_CREATE and state.step == STEP_AWAIT_TITLE:
        return ok("Что напомнить? Напиши текст напоминания.", intent="wizard.reminder_create.title", mode="local", actions=_step_actions())
    if state.wizard_id == WIZARD_REMINDER_CREATE and state.step == STEP_AWAIT_DATETIME:
        return ok(
            "Пришли дату и время в формате YYYY-MM-DD HH:MM или DD.MM.YYYY HH:MM.\n"
            "Можно: сегодня 18:30, 07.02 12:00 или через 10 минут.",
            intent="wizard.reminder_create.datetime",
            mode="local",
            actions=_step_actions(),
        )
    if state.wizard_id == WIZARD_REMINDER_CREATE and state.step == STEP_AWAIT_RECURRENCE:
        return ok(
            "Нужен повтор? Напиши: none/daily/weekdays/weekly:1,3,5/monthly:15.\n"
            "Интервал: daily/2, weekdays/2, weekly:1,3/2, monthly:15/2.",
            intent="wizard.reminder_create.recurrence",
            mode="local",
            actions=_step_actions(),
        )
    if state.wizard_id == WIZARD_REMINDER_CREATE and state.step == STEP_CONFIRM:
        title = state.data.get("title") if isinstance(state.data.get("title"), str) else "без текста"
        trigger_raw = state.data.get("trigger_at")
        trigger = datetime.fromisoformat(trigger_raw) if isinstance(trigger_raw, str) else None
        recurrence_value = state.data.get("recurrence")
        recurrence = recurrence_value if isinstance(recurrence_value, dict) else None
        display_dt = trigger.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M") if isinstance(trigger, datetime) else "неизвестно"
        return ok(
            f"Создать напоминание: {title}\nКогда: {display_dt} (МСК)\nПовтор: {_recurrence_label(recurrence)}?",
            intent="wizard.reminder_create.confirm",
            mode="local",
            actions=_confirm_actions(),
        )
    if state.wizard_id == WIZARD_REMINDER_RESCHEDULE and state.step == STEP_AWAIT_DATETIME:
        return ok(
            "Пришли новую дату и время в формате YYYY-MM-DD HH:MM или DD.MM.YYYY HH:MM.\n"
            "Можно: сегодня 18:30, 07.02 12:00 или через 10 минут.",
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
            new_trigger.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
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
            "Можно одной строкой с названием: завтра 19:00 врач, через 2 часа тренировка, в пятницу 10:15 встреча.\n"
            "Пример строгого формата: 2026-02-05 18:30 или 05.02.2026 18:30",
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
            dt.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
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




def _parse_recurrence_input(raw: str) -> dict[str, object] | None:
    value = raw.strip().lower()
    if value in {"", "none", "нет", "no"}:
        return None
    base_value, interval = _split_recurrence_interval(value)
    if base_value == "daily":
        recurrence: dict[str, object] = {"freq": "daily"}
        if interval:
            recurrence["interval"] = interval
        return recurrence
    if base_value == "weekdays":
        recurrence = {"freq": "weekdays"}
        if interval:
            recurrence["interval"] = interval
        return recurrence
    if base_value.startswith("weekly"):
        if ":" not in base_value:
            recurrence = {"freq": "weekly", "byweekday": [0]}
        else:
            _, days = base_value.split(":", 1)
            values = []
            for part in days.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    day = int(part)
                except ValueError as exc:
                    raise ValueError("weekly: укажи дни 0..6 через запятую") from exc
                if day < 0 or day > 6:
                    raise ValueError("weekly: укажи дни 0..6")
                values.append(day)
            if not values:
                raise ValueError("weekly: укажи минимум один день 0..6")
            recurrence = {"freq": "weekly", "byweekday": sorted(set(values))}
        if interval:
            recurrence["interval"] = interval
        return recurrence
    if base_value.startswith("monthly"):
        if ":" not in base_value:
            raise ValueError("monthly: укажи число месяца, например monthly:15")
        _, day_raw = base_value.split(":", 1)
        try:
            day = int(day_raw.strip())
        except ValueError as exc:
            raise ValueError("monthly: число 1..31") from exc
        if day < 1 or day > 31:
            raise ValueError("monthly: число 1..31")
        recurrence = {"freq": "monthly", "bymonthday": day}
        if interval:
            recurrence["interval"] = interval
        return recurrence
    raise ValueError("Повтор: none/daily/weekdays/weekly:1,3,5/monthly:15")


def _split_recurrence_interval(value: str) -> tuple[str, int | None]:
    interval: int | None = None
    base = value
    if "/" in value:
        base, interval_raw = value.split("/", 1)
    elif value.startswith(("daily:", "weekdays:")):
        base, interval_raw = value.split(":", 1)
    else:
        interval_raw = ""
    if interval_raw:
        try:
            interval = int(interval_raw.strip())
        except ValueError as exc:
            raise ValueError("Интервал: укажи целое число") from exc
        if interval < 1:
            raise ValueError("Интервал должен быть >= 1")
    return base, interval


def _recurrence_label(recurrence: dict[str, object] | None) -> str:
    if not recurrence:
        return "без повтора"
    freq = recurrence.get("freq")
    interval = recurrence.get("interval")
    interval_value = interval if isinstance(interval, int) and interval > 1 else None
    if freq == "weekly":
        days = recurrence.get("byweekday")
        if isinstance(days, list):
            days_label = ",".join(str(x) for x in days)
            if interval_value:
                return f"каждые {interval_value} недель ({days_label})"
            return f"еженедельно ({days_label})"
    if freq == "monthly":
        day_label = recurrence.get("bymonthday")
        if interval_value:
            return f"каждые {interval_value} месяцев ({day_label})"
        return f"ежемесячно ({day_label})"
    if freq == "weekdays":
        if interval_value:
            return f"по будням (каждые {interval_value} недель)"
        return "по будням"
    if freq == "daily":
        if interval_value:
            return f"каждые {interval_value} дней"
        return "ежедневно"
    if isinstance(freq, str):
        return freq
    return "без повтора"
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


def _resume_actions(wizard_id: str, *, resume_target: str | None = None) -> list[Action]:
    target_label = _wizard_target_label(resume_target)
    restart_payload: dict[str, object] = {"op": "wizard.restart"}
    if target_label:
        restart_payload["target"] = target_label
    return [
        Action(id="wizard.resume", label="▶️ Продолжить", payload={"op": "wizard.resume"}),
        Action(id="wizard.restart", label="🔄 Начать заново", payload=restart_payload),
        Action(id="wizard.cancel", label="❌ Отмена", payload={"op": "wizard.cancel"}),
    ]


def _wizard_target_label(wizard_id: str | None) -> str | None:
    if wizard_id == WIZARD_REMINDER_CREATE:
        return "reminders.create"
    if wizard_id == WIZARD_CALENDAR_ADD:
        return "calendar.add"
    if wizard_id == WIZARD_REMINDER_RESCHEDULE:
        return "reminder.reschedule"
    return None


def _post_create_actions(event_id: str) -> list[Action]:
    actions = [
        Action(
            id="reminder.add_10m",
            label="➕ Добавить напоминание (10 мин до)",
            payload={"op": "reminder_add_offset", "event_id": event_id, "minutes": 10},
        ),
        Action(
            id="utility_reminders.list",
            label="📋 Показать ближайшие",
            payload={"op": "reminder.list"},
        ),
        Action(id="menu.open", label="🏠 Меню", payload={"op": "menu_open"}),
    ]
    return actions
