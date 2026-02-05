from __future__ import annotations

from datetime import datetime

from app.core import calendar_store
from app.core.result import Action, OrchestratorResult, ensure_valid, ok, refused


async def list_calendar_items(
    start: datetime | None,
    end: datetime | None,
    *,
    intent: str = "utility_calendar.list",
) -> OrchestratorResult:
    items = await calendar_store.list_items(start, end)
    if not items:
        return ensure_valid(ok("Нет событий.", intent=intent, mode="tool"))
    if len(items) > 20:
        return ensure_valid(refused("Слишком много, сузь диапазон.", intent=intent, mode="tool"))
    lines = []
    for item in items:
        dt_label = item.dt.astimezone(calendar_store.MOSCOW_TZ).strftime("%Y-%m-%d %H:%M")
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
        when_label = item.trigger_at.astimezone(calendar_store.MOSCOW_TZ).strftime("%Y-%m-%d %H:%M")
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
