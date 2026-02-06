from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from app.core import calendar_backend, calendar_store, tools_calendar_caldav
from app.core.result import Action, OrchestratorResult, ensure_valid, ok, refused

LOGGER = logging.getLogger(__name__)

_NOT_CONNECTED_TEXT = "Календарь не подключён: задайте CALDAV_URL/USERNAME/PASSWORD."


def _get_calendar_backend(user_id: int, chat_id: int) -> calendar_backend.CalendarBackend:
    """Get the configured calendar backend with fallback to local."""
    backend_type = os.getenv("CALENDAR_BACKEND", "local").strip().lower()

    if backend_type == "caldav":
        config = tools_calendar_caldav.load_caldav_config()
        if config is not None:
            return calendar_backend.CalDAVCalendarBackend(config)
        LOGGER.warning(
            "CALENDAR_BACKEND=caldav but CalDAV not configured, falling back to local"
        )

    return calendar_backend.LocalCalendarBackend(user_id=user_id, chat_id=chat_id)


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

    backend_type = os.getenv("CALENDAR_BACKEND", "local").strip().lower()
    backend = _get_calendar_backend(user_id, chat_id)
    result_debug: dict[str, object] = {}

    try:
        created = await backend.create_event(
            title=title,
            start_dt=start_at,
            end_dt=start_at + timedelta(hours=1),
            tz=calendar_store.BOT_TZ,
        )
        event_id = created.uid

        # Also store in local calendar_store for reminders compatibility
        await calendar_store.add_item(
            dt=start_at,
            title=title,
            chat_id=chat_id,
            remind_at=None,
            user_id=user_id,
            reminders_enabled=False,
            event_id=event_id,
        )

        result_debug["calendar_backend"] = created.backend
        if created.calendar_name:
            result_debug["caldav_calendar"] = created.calendar_name
        if created.uid:
            result_debug["caldav_uid"] = created.uid
        if created.url_base:
            result_debug["caldav_url_base"] = created.url_base

        LOGGER.info(
            "calendar.create ok: event_id=%s backend=%s",
            event_id,
            created.backend,
        )

        dt_label = start_at.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
        text = f"Событие создано: {title} | {dt_label}"
        return ensure_valid(ok(text, intent=intent, mode="tool", debug=result_debug))

    except Exception as exc:
        error_class = exc.__class__.__name__
        LOGGER.error(
            "calendar.create error: request_id=%s user_id=%s backend=%s error=%s",
            request_label,
            user_id,
            backend_type,
            error_class,
        )

        # Fallback to local if CalDAV failed
        if backend_type == "caldav":
            LOGGER.info(
                "calendar.create fallback: request_id=%s user_id=%s trying local",
                request_label,
                user_id,
            )
            try:
                local_backend = calendar_backend.LocalCalendarBackend(
                    user_id=user_id, chat_id=chat_id
                )
                created = await local_backend.create_event(
                    title=title,
                    start_dt=start_at,
                    end_dt=start_at + timedelta(hours=1),
                    tz=calendar_store.BOT_TZ,
                )

                result_debug["calendar_backend"] = "local_fallback"
                result_debug["caldav_error"] = error_class

                LOGGER.info(
                    "calendar.create fallback ok: event_id=%s",
                    created.uid,
                )

                dt_label = start_at.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
                text = f"Событие создано (локально): {title} | {dt_label}"
                return ensure_valid(ok(text, intent=intent, mode="tool", debug=result_debug))

            except Exception as fallback_exc:
                LOGGER.error(
                    "calendar.create fallback error: request_id=%s error=%s",
                    request_label,
                    fallback_exc.__class__.__name__,
                )

        return ensure_valid(
            refused(
                "Не удалось создать событие.",
                intent=intent,
                mode="tool",
                debug={"error": error_class},
            )
        )


async def delete_event(
    item_id: str,
    *,
    user_id: int,
    chat_id: int,
    intent: str = "utility_calendar.del",
) -> OrchestratorResult:
    backend = _get_calendar_backend(user_id, chat_id)

    try:
        deleted_remote = await backend.delete_event(event_id=item_id)
    except Exception as exc:
        LOGGER.error("calendar.delete error: user_id=%s error=%s", user_id, exc.__class__.__name__)
        deleted_remote = False

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
    chat_id: int,
    intent: str = "utility_calendar.list",
) -> OrchestratorResult:
    backend = _get_calendar_backend(user_id, chat_id)
    start_value = start or datetime.now(tz=timezone.utc)
    end_value = end or (start_value + timedelta(days=7))

    try:
        events = await backend.list_events(start=start_value, end=end_value, limit=20)
    except Exception as exc:
        LOGGER.error("calendar.list error: user_id=%s error=%s", user_id, exc.__class__.__name__)

        # Fallback to local
        if isinstance(backend, calendar_backend.CalDAVCalendarBackend):
            LOGGER.info("calendar.list fallback: user_id=%s trying local", user_id)
            try:
                local_backend = calendar_backend.LocalCalendarBackend(user_id=user_id, chat_id=chat_id)
                events = await local_backend.list_events(start=start_value, end=end_value, limit=20)
            except Exception:
                return ensure_valid(
                    refused(
                        "Не удалось получить список событий.",
                        intent=intent,
                        mode="tool",
                        debug={"error": exc.__class__.__name__},
                    )
                )
        else:
            return ensure_valid(
                refused(
                    "Не удалось получить список событий.",
                    intent=intent,
                    mode="tool",
                    debug={"error": exc.__class__.__name__},
                )
            )

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
