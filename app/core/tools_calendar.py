from __future__ import annotations

import logging
import os
from datetime import datetime

from app.core import calendar_store
from app.core.result import Action, OrchestratorResult, ensure_valid, ok, refused

LOGGER = logging.getLogger(__name__)

_NOT_CONNECTED_TEXT = (
    "Календарь не подключён. Нужно авторизоваться/подключить Google Calendar в настройках."
)


def _is_calendar_connected() -> bool:
    for key in ("CALENDAR_CONNECTED", "GOOGLE_CALENDAR_TOKEN", "CALENDAR_TOKEN", "CALENDAR_CREDENTIALS"):
        value = os.getenv(key)
        if value and value.strip().lower() not in {"0", "false", "no"}:
            return True
    return False


async def create_event(
    *,
    start_at: datetime,
    title: str,
    chat_id: int,
    user_id: int,
    request_id: str | None = None,
    intent: str = "utility_calendar.add",
) -> OrchestratorResult:
    request_label = request_id or "-"
    LOGGER.info(
        "calendar.create start: request_id=%s user_id=%s start_at=%s title=%r",
        request_label,
        user_id,
        start_at.isoformat(),
        title,
    )
    if not _is_calendar_connected():
        LOGGER.info(
            "calendar.create refused: request_id=%s user_id=%s reason=calendar_not_connected",
            request_label,
            user_id,
        )
        return ensure_valid(refused(_NOT_CONNECTED_TEXT, intent=intent, mode="tool", debug={"reason": "not_connected"}))
    try:
        created = await calendar_store.add_item(
            dt=start_at,
            title=title,
            chat_id=chat_id,
            remind_at=None,
            user_id=user_id,
            reminders_enabled=False,
        )
    except Exception as exc:
        LOGGER.exception("calendar.create error: request_id=%s user_id=%s error=%s", request_label, user_id, exc)
        return ensure_valid(refused("Не удалось создать событие.", intent=intent, mode="tool", debug={"reason": "error"}))
    event = created.get("event") if isinstance(created, dict) else None
    event_id = event.get("event_id") if isinstance(event, dict) else None
    if not isinstance(event_id, str):
        LOGGER.error("calendar.create error: request_id=%s user_id=%s reason=missing_event_id", request_label, user_id)
        return ensure_valid(refused("Не удалось создать событие.", intent=intent, mode="tool", debug={"reason": "missing_event_id"}))
    LOGGER.info("calendar.create ok: event_id=%s", event_id)
    dt_label = start_at.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
    text = f"Событие добавлено: {event_id} | {dt_label} | {title}"
    return ensure_valid(ok(text, intent=intent, mode="tool", debug={"event_id": event_id}))


async def delete_event(
    item_id: str,
    *,
    intent: str = "utility_calendar.del",
) -> OrchestratorResult:
    if not _is_calendar_connected():
        LOGGER.info("calendar.delete refused: reason=calendar_not_connected")
        return ensure_valid(refused(_NOT_CONNECTED_TEXT, intent=intent, mode="tool", debug={"reason": "not_connected"}))
    removed, reminder_id = await calendar_store.delete_item(item_id)
    text = f"Удалено: {item_id}" if removed else f"Не найдено: {item_id}"
    result = ok(text, intent=intent, mode="tool") if removed else refused(text, intent=intent, mode="tool")
    if reminder_id:
        return ensure_valid(
            ok(text, intent=intent, mode="tool", debug={"reminder_id": reminder_id})
            if removed
            else refused(text, intent=intent, mode="tool", debug={"reminder_id": reminder_id})
        )
    return ensure_valid(result)


async def list_calendar_items(
    start: datetime | None,
    end: datetime | None,
    *,
    intent: str = "utility_calendar.list",
) -> OrchestratorResult:
    if not _is_calendar_connected():
        LOGGER.info("calendar.list refused: reason=calendar_not_connected")
        return ensure_valid(refused(_NOT_CONNECTED_TEXT, intent=intent, mode="tool", debug={"reason": "not_connected"}))
    items = await calendar_store.list_items(start, end)
    if not items:
        return ensure_valid(ok("Нет событий.", intent=intent, mode="tool"))
    if len(items) > 20:
        return ensure_valid(refused("Слишком много, сузь диапазон.", intent=intent, mode="tool"))
    lines = []
    for item in items:
        dt_label = item.dt.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{item.id} | {dt_label} | {item.title}")
    return ensure_valid(ok("\n".join(lines), intent=intent, mode="tool"))


async def list_reminders(
    now: datetime,
    *,
    limit: int,
    intent: str = "utility_reminders.list",
) -> OrchestratorResult:
    items = await calendar_store.list_reminders(now, limit=limit)
    if not items:
        return ensure_valid(ok("Нет запланированных напоминаний.", intent=intent, mode="tool"))
    lines = []
    actions: list[Action] = []
    for item in items:
        when_label = item.trigger_at.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{item.id} | {when_label} | {item.text}")
        actions.append(
            Action(
                id=f"reminder_snooze:{item.id}:10",
                label="⏸ Отложить на 10 минут",
                payload={
                    "op": "reminder_snooze",
                    "reminder_id": item.id,
                    "minutes": 10,
                    "base_trigger_at": item.trigger_at.isoformat(),
                },
            )
        )
        actions.append(
            Action(
                id=f"reminder_reschedule:{item.id}",
                label="✏ Перенести",
                payload={"op": "reminder_reschedule", "reminder_id": item.id, "base_trigger_at": item.trigger_at.isoformat()},
            )
        )
        actions.append(
            Action(
                id=f"reminder_disable:{item.id}",
                label="🗑 Отключить",
                payload={"op": "reminder_disable", "reminder_id": item.id},
            )
        )
    actions.append(Action(id="menu.open", label="🏠 Меню", payload={"op": "menu_open"}))
    return ensure_valid(ok("\n".join(lines), intent=intent, mode="tool", actions=actions))
