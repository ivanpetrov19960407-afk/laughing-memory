from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from app.core import calendar_store, tools_calendar_caldav
from app.core.calendar_backend_caldav import CalDAVCalendarBackend, load_caldav_backend_config
from app.core.calendar_backend_local import LocalCalendarBackend
from app.core.result import Action, OrchestratorResult, ensure_valid, ok, refused

LOGGER = logging.getLogger(__name__)

_NOT_CONNECTED_TEXT = "Календарь не подключён: задайте CALDAV_URL/USERNAME/PASSWORD."


def _calendar_backend_env() -> str:
    value = (os.getenv("CALENDAR_BACKEND") or "local").strip().lower()
    return value if value in {"local", "caldav"} else "local"


def _safe_caldav_error_label(exc: Exception) -> str:
    # Never include exception message (may contain URLs / user input).
    # Best-effort: include only class name + possible status code encoded in our RuntimeError.
    name = exc.__class__.__name__
    if isinstance(exc, RuntimeError):
        msg = str(exc)
        if msg == "caldav_not_configured":
            return msg
        if msg.startswith(("caldav_put_status:", "caldav_put_failed:")):
            return msg
    return name


async def caldav_check_connection() -> tuple[bool, str | None]:
    """
    UI helper: check CalDAV connectivity and return (ok, calendar_name).

    Never raises; never includes secrets in return values/logs.
    """
    config = tools_calendar_caldav.load_caldav_config()
    if config is None:
        return False, None
    try:
        ok_status, calendar_name = await tools_calendar_caldav.check_connection(config)
        return bool(ok_status), calendar_name
    except Exception as exc:
        LOGGER.warning("calendar.caldav check failed: error=%s", exc.__class__.__name__)
        return False, None


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
    # Ensure timezone-aware datetimes (project default: Europe/Vilnius via BOT_TZ).
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=calendar_store.BOT_TZ)
    end_at = start_at + timedelta(hours=1)
    backend_name = _calendar_backend_env()
    backend_debug: dict[str, object] = {}
    try:
        if backend_name == "caldav":
            config = load_caldav_backend_config()
            if config is None:
                raise RuntimeError("caldav_not_configured")
            backend = CalDAVCalendarBackend(config)
        else:
            backend = LocalCalendarBackend()

        created_backend = await backend.create_event(
            title=title,
            start_dt=start_at,
            end_dt=end_at,
            description=None,
            location=None,
            tz=str(calendar_store.BOT_TZ),
        )
        event_id = created_backend.uid
        backend_debug = dict(created_backend.debug or {})
    except Exception as exc:
        if backend_name == "caldav":
            safe_error = _safe_caldav_error_label(exc)
            LOGGER.error(
                "calendar.create caldav error: request_id=%s user_id=%s error=%s",
                request_label,
                user_id,
                safe_error,
            )
            # Fallback to local backend.
            try:
                local_backend = LocalCalendarBackend()
                created_backend = await local_backend.create_event(
                    title=title,
                    start_dt=start_at,
                    end_dt=end_at,
                    description=None,
                    location=None,
                    tz=str(calendar_store.BOT_TZ),
                )
                event_id = created_backend.uid
                backend_debug = dict(created_backend.debug or {})
                backend_debug["calendar_backend"] = "local_fallback"
                backend_debug["caldav_error"] = safe_error
            except Exception as local_exc:
                LOGGER.error(
                    "calendar.create local fallback failed: request_id=%s user_id=%s error=%s",
                    request_label,
                    user_id,
                    local_exc.__class__.__name__,
                )
                return ensure_valid(
                    refused("Не удалось создать событие.", intent=intent, mode="tool", debug={"reason": "error"})
                )
        else:
            LOGGER.error(
                "calendar.create error: request_id=%s user_id=%s error=%s",
                request_label,
                user_id,
                exc.__class__.__name__,
            )
            return ensure_valid(
                refused("Не удалось создать событие.", intent=intent, mode="tool", debug={"reason": "error"})
            )

    # Persist to local calendar store (used for reminders and UI).
    try:
        created = await calendar_store.add_item(
            dt=start_at,
            title=title,
            chat_id=chat_id,
            remind_at=None,
            user_id=user_id,
            reminders_enabled=False,
            event_id=event_id,
        )
    except Exception as exc:
        LOGGER.error(
            "calendar.store error: request_id=%s user_id=%s error=%s",
            request_label,
            user_id,
            exc.__class__.__name__,
        )
        return ensure_valid(refused("Не удалось создать событие.", intent=intent, mode="tool", debug={"reason": "store_error"}))
    event = created.get("event") if isinstance(created, dict) else None
    event_id = event.get("event_id") if isinstance(event, dict) else None
    if not isinstance(event_id, str):
        LOGGER.error("calendar.create error: request_id=%s user_id=%s reason=missing_event_id", request_label, user_id)
        return ensure_valid(refused("Не удалось создать событие.", intent=intent, mode="tool", debug={"reason": "missing_event_id"}))
    LOGGER.info("calendar.create ok: event_id=%s", event_id)
    dt_label = start_at.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
    text = f"Событие создано: {dt_label} | {title}"
    debug = {"event_id": event_id}
    debug.update({k: v for k, v in (backend_debug or {}).items() if k and v is not None})
    # Ensure presence of backend marker for local default.
    if "calendar_backend" not in debug:
        debug["calendar_backend"] = "local"
    return ensure_valid(ok(text, intent=intent, mode="tool", debug=debug))


async def delete_event(
    item_id: str,
    *,
    user_id: int,
    intent: str = "utility_calendar.del",
) -> OrchestratorResult:
    deleted_remote = False
    if _calendar_backend_env() == "caldav":
        config = tools_calendar_caldav.load_caldav_config()
        if config is not None:
            try:
                deleted_remote = await tools_calendar_caldav.delete_event(config, event_id=item_id)
            except Exception as exc:
                LOGGER.error("calendar.delete caldav error: user_id=%s error=%s", user_id, exc.__class__.__name__)
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
    start_value = start or datetime.now(tz=timezone.utc)
    end_value = end or (start_value + timedelta(days=7))
    try:
        items = await calendar_store.list_items(start_value, end_value)
    except Exception as exc:
        LOGGER.error("calendar.list local error: user_id=%s error=%s", user_id, exc.__class__.__name__)
        return ensure_valid(refused("Не удалось получить список событий.", intent=intent, mode="tool", debug={"reason": "error"}))
    if not items:
        return ensure_valid(ok("Нет событий.", intent=intent, mode="tool"))
    lines: list[str] = []
    for item in items[:20]:
        dt_label = item.dt.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{item.id} | {dt_label} | {item.title}")
    return ensure_valid(ok("\n".join(lines), intent=intent, mode="tool", debug={"calendar_backend": _calendar_backend_env()}))


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
