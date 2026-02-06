from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from app.core import calendar_store, tools_calendar_caldav
from app.core.calendar_backend import (
    CalDAVCalendarBackend,
    CalendarBackend,
    CalendarCreateResult,
    LocalCalendarBackend,
    get_backend,
)
from app.core.result import Action, OrchestratorResult, ensure_valid, ok, refused

LOGGER = logging.getLogger(__name__)

_NOT_CONNECTED_TEXT = "Календарь не подключён: задайте CALDAV_URL/USERNAME/PASSWORD."

# Regex to redact passwords from error messages (safety net)
_PASSWORD_RE = re.compile(r"(password|passwd|pwd|secret|token)=\S+", re.IGNORECASE)


def _safe_error_class(exc: Exception) -> str:
    """Return class name of exception — never include password/secret."""
    text = exc.__class__.__name__
    # Extra caution: strip any query params that might contain secrets
    return _PASSWORD_RE.sub(r"\1=***", text)


async def create_event(
    *,
    start_at: datetime,
    title: str,
    chat_id: int,
    user_id: int,
    request_id: str | None = None,
    intent: str = "calendar.add",
) -> OrchestratorResult:
    request_label = request_id or "-"
    LOGGER.info(
        "calendar.create start: request_id=%s user_id=%s start_at=%s title=%r",
        request_label,
        user_id,
        start_at.isoformat(),
        title,
    )

    backend = get_backend()
    end_at = start_at + timedelta(hours=1)

    # --- Attempt primary backend ---
    result: CalendarCreateResult | None = None
    used_fallback = False
    caldav_error: str | None = None

    try:
        result = await backend.create_event(
            title,
            start_at,
            end_at,
            description=None,
        )
    except Exception as exc:
        caldav_error = _safe_error_class(exc)
        LOGGER.error(
            "calendar.create caldav error: request_id=%s user_id=%s error=%s",
            request_label,
            user_id,
            caldav_error,
        )

    # --- Fallback: if CalDAV backend failed, try local ---
    if result is None or not result.success:
        if isinstance(backend, CalDAVCalendarBackend):
            LOGGER.info(
                "calendar.create fallback to local: request_id=%s user_id=%s",
                request_label,
                user_id,
            )
            used_fallback = True
            try:
                local_backend = LocalCalendarBackend()
                result = await local_backend.create_event(
                    title,
                    start_at,
                    end_at,
                    description=None,
                )
            except Exception as exc2:
                LOGGER.error(
                    "calendar.create local fallback error: request_id=%s user_id=%s error=%s",
                    request_label,
                    user_id,
                    exc2.__class__.__name__,
                )
                return ensure_valid(
                    refused(
                        "Не удалось создать событие.",
                        intent=intent,
                        mode="tool",
                        debug={"reason": "error"},
                    )
                )

    if result is None or not result.success:
        error_reason = result.error if result else "unknown"
        LOGGER.error(
            "calendar.create failed: request_id=%s user_id=%s reason=%s",
            request_label,
            user_id,
            error_reason,
        )
        return ensure_valid(
            refused(
                "Не удалось создать событие.",
                intent=intent,
                mode="tool",
                debug={"reason": error_reason or "error"},
            )
        )

    # --- Always persist locally (for reminders, local list, etc.) ---
    event_id = result.event_id or result.uid
    if event_id:
        try:
            await calendar_store.add_item(
                dt=start_at,
                title=title,
                chat_id=chat_id,
                remind_at=None,
                user_id=user_id,
                reminders_enabled=False,
                event_id=event_id,
            )
        except Exception:
            pass  # local persistence is best-effort

    if not isinstance(event_id, str) or not event_id:
        LOGGER.error(
            "calendar.create error: request_id=%s user_id=%s reason=missing_event_id",
            request_label,
            user_id,
        )
        return ensure_valid(
            refused(
                "Не удалось создать событие.",
                intent=intent,
                mode="tool",
                debug={"reason": "missing_event_id"},
            )
        )

    LOGGER.info("calendar.create ok: event_id=%s backend=%s", event_id, result.backend)
    dt_label = start_at.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
    text = f"Событие создано: {title} ({dt_label})"

    # Build debug
    debug: dict[str, object] = {"event_id": event_id}
    if used_fallback:
        debug["calendar_backend"] = "local_fallback"
        if caldav_error:
            debug["caldav_error"] = caldav_error
    elif result.debug:
        debug.update(result.debug)
    else:
        debug["calendar_backend"] = result.backend

    return ensure_valid(ok(text, intent=intent, mode="tool", debug=debug))


async def delete_event(
    item_id: str,
    *,
    user_id: int,
    intent: str = "utility_calendar.del",
) -> OrchestratorResult:
    config = tools_calendar_caldav.load_caldav_config()
    if config is None:
        LOGGER.info("calendar.delete refused: reason=calendar_not_connected user_id=%s", user_id)
        return ensure_valid(
            refused(_NOT_CONNECTED_TEXT, intent=intent, mode="tool", debug={"reason": "calendar_not_connected"})
        )
    try:
        deleted_remote = await tools_calendar_caldav.delete_event(config, event_id=item_id)
    except Exception as exc:
        LOGGER.error("calendar.delete error: user_id=%s error=%s", user_id, exc.__class__.__name__)
        return ensure_valid(refused("Не удалось удалить событие.", intent=intent, mode="tool", debug={"reason": "error"}))
    removed, reminder_id = await calendar_store.delete_item(item_id)
    deleted = deleted_remote or removed
    text = f"Удалено: {item_id}" if deleted else f"Не найдено: {item_id}"
    result = ok(text, intent=intent, mode="tool") if deleted else refused(text, intent=intent, mode="tool")
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
    user_id: int,
    intent: str = "utility_calendar.list",
) -> OrchestratorResult:
    config = tools_calendar_caldav.load_caldav_config()
    if config is None:
        LOGGER.info("calendar.list refused: reason=calendar_not_connected user_id=%s", user_id)
        return ensure_valid(
            refused(_NOT_CONNECTED_TEXT, intent=intent, mode="tool", debug={"reason": "calendar_not_connected"})
        )
    start_value = start or datetime.now(tz=timezone.utc)
    end_value = end or (start_value + timedelta(days=7))
    try:
        events = await tools_calendar_caldav.list_events(config, start=start_value, end=end_value, limit=20)
    except Exception as exc:
        LOGGER.error("calendar.list error: user_id=%s error=%s", user_id, exc.__class__.__name__)
        return ensure_valid(refused("Не удалось получить список событий.", intent=intent, mode="tool", debug={"reason": "error"}))
    if not events:
        return ensure_valid(ok("Нет событий.", intent=intent, mode="tool"))
    lines = []
    for item in events:
        dt_label = item.start_at.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{item.uid} | {dt_label} | {item.summary}")
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
