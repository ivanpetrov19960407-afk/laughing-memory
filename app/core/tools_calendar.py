from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from app.core import calendar_store, tools_calendar_caldav
from app.core.calendar_backend import CalDAVCalendarBackend, LocalCalendarBackend
from app.core.result import Action, OrchestratorResult, ensure_valid, ok, refused

LOGGER = logging.getLogger(__name__)

_CALENDAR_BACKEND_ENV = "CALENDAR_BACKEND"
_DEFAULT_BACKEND = "local"


def _resolve_backend_mode() -> str:
    raw = os.getenv(_CALENDAR_BACKEND_ENV, _DEFAULT_BACKEND)
    normalized = raw.strip().lower() if isinstance(raw, str) else _DEFAULT_BACKEND
    if normalized in {"local", "caldav"}:
        return normalized
    if normalized:
        LOGGER.warning("calendar.backend.invalid: value=%r fallback=local", normalized)
    return _DEFAULT_BACKEND


def _safe_caldav_error_label(exc: Exception) -> str:
    if isinstance(exc, tools_calendar_caldav.CalDAVRequestError):
        return f"{exc.__class__.__name__}:{exc.status_code}"
    return exc.__class__.__name__


def _ensure_aware_for_label(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=calendar_store.BOT_TZ)
    return value


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
    backend_mode = _resolve_backend_mode()
    end_at = start_at + timedelta(hours=1)
    if backend_mode == "caldav":
        config = tools_calendar_caldav.load_caldav_config()
        if config is None:
            LOGGER.info(
                "calendar.create fallback: request_id=%s user_id=%s reason=caldav_missing_config",
                request_label,
                user_id,
            )
            return await _create_event_local_fallback(
                start_at=start_at,
                end_at=end_at,
                title=title,
                chat_id=chat_id,
                user_id=user_id,
                intent=intent,
                caldav_error="missing_config",
            )
        try:
            backend = CalDAVCalendarBackend(config, chat_id=chat_id, user_id=user_id)
            created = await backend.create_event(title=title, start_dt=start_at, end_dt=end_at)
            return _build_create_result(
                created,
                start_at=start_at,
                title=title,
                intent=intent,
                calendar_backend="caldav",
            )
        except Exception as exc:
            LOGGER.error(
                "calendar.create caldav error: request_id=%s user_id=%s error=%s",
                request_label,
                user_id,
                exc.__class__.__name__,
            )
            return await _create_event_local_fallback(
                start_at=start_at,
                end_at=end_at,
                title=title,
                chat_id=chat_id,
                user_id=user_id,
                intent=intent,
                caldav_error=_safe_caldav_error_label(exc),
            )
    try:
        backend = LocalCalendarBackend(chat_id=chat_id, user_id=user_id, reminders_enabled=False)
        created = await backend.create_event(title=title, start_dt=start_at, end_dt=end_at)
    except Exception as exc:
        LOGGER.error(
            "calendar.create local error: request_id=%s user_id=%s error=%s",
            request_label,
            user_id,
            exc.__class__.__name__,
        )
        return ensure_valid(refused("Не удалось создать событие.", intent=intent, mode="tool", debug={"reason": "error"}))
    return _build_create_result(created, start_at=start_at, title=title, intent=intent, calendar_backend="local")


def _build_create_result(
    created,
    *,
    start_at: datetime,
    title: str,
    intent: str,
    calendar_backend: str,
    caldav_error: str | None = None,
) -> OrchestratorResult:
    event_id = getattr(created, "event_id", None)
    if not isinstance(event_id, str) or not event_id:
        return ensure_valid(refused("Не удалось создать событие.", intent=intent, mode="tool", debug={"reason": "missing_event_id"}))
    start_value = _ensure_aware_for_label(start_at)
    dt_label = start_value.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
    text = f"Событие создано: {dt_label} | {title}"
    debug: dict[str, str] = {"event_id": event_id, "calendar_backend": calendar_backend}
    created_debug = getattr(created, "debug", None)
    if isinstance(created_debug, dict):
        for key, value in created_debug.items():
            if isinstance(key, str) and isinstance(value, str):
                debug[key] = value
    if caldav_error:
        debug["caldav_error"] = caldav_error
    return ensure_valid(ok(text, intent=intent, mode="tool", debug=debug))


async def _create_event_local_fallback(
    *,
    start_at: datetime,
    end_at: datetime,
    title: str,
    chat_id: int,
    user_id: int,
    intent: str,
    caldav_error: str,
) -> OrchestratorResult:
    try:
        backend = LocalCalendarBackend(chat_id=chat_id, user_id=user_id, reminders_enabled=False)
        created = await backend.create_event(title=title, start_dt=start_at, end_dt=end_at)
    except Exception as exc:
        LOGGER.error(
            "calendar.create local fallback error: user_id=%s error=%s",
            user_id,
            exc.__class__.__name__,
        )
        return ensure_valid(refused("Не удалось создать событие.", intent=intent, mode="tool", debug={"reason": "error"}))
    return _build_create_result(
        created,
        start_at=start_at,
        title=title,
        intent=intent,
        calendar_backend="local_fallback",
        caldav_error=caldav_error,
    )


async def delete_event(
    item_id: str,
    *,
    user_id: int,
    intent: str = "utility_calendar.delete",
) -> OrchestratorResult:
    backend_mode = _resolve_backend_mode()
    deleted_remote = False
    caldav_error: str | None = None
    if backend_mode == "caldav":
        config = tools_calendar_caldav.load_caldav_config()
        if config is None:
            caldav_error = "missing_config"
            LOGGER.info("calendar.delete fallback: user_id=%s reason=caldav_missing_config", user_id)
        else:
            try:
                deleted_remote = await tools_calendar_caldav.delete_event(config, event_id=item_id)
            except Exception as exc:
                caldav_error = _safe_caldav_error_label(exc)
                LOGGER.error("calendar.delete caldav error: user_id=%s error=%s", user_id, exc.__class__.__name__)
    removed, reminder_id = await calendar_store.delete_item(item_id)
    deleted = deleted_remote or removed
    text = f"Удалено: {item_id}" if deleted else f"Не найдено: {item_id}"
    debug: dict[str, object] = {}
    if reminder_id:
        debug["reminder_id"] = reminder_id
    if caldav_error:
        debug["calendar_backend"] = "local_fallback"
        debug["caldav_error"] = caldav_error
    result = ok(text, intent=intent, mode="tool", debug=debug) if deleted else refused(text, intent=intent, mode="tool", debug=debug)
    return ensure_valid(result)


async def list_calendar_items(
    start: datetime | None,
    end: datetime | None,
    *,
    user_id: int,
    intent: str = "utility_calendar.list",
) -> OrchestratorResult:
    backend_mode = _resolve_backend_mode()
    start_value = start or datetime.now(tz=calendar_store.BOT_TZ)
    end_value = end or (start_value + timedelta(days=7))
    if backend_mode == "caldav":
        config = tools_calendar_caldav.load_caldav_config()
        if config is None:
            LOGGER.info("calendar.list fallback: user_id=%s reason=caldav_missing_config", user_id)
            return await _list_local_items(
                start_value,
                end_value,
                intent=intent,
                caldav_error="missing_config",
            )
        try:
            events = await tools_calendar_caldav.list_events(config, start=start_value, end=end_value, limit=20)
        except Exception as exc:
            LOGGER.error("calendar.list caldav error: user_id=%s error=%s", user_id, exc.__class__.__name__)
            return await _list_local_items(
                start_value,
                end_value,
                intent=intent,
                caldav_error=_safe_caldav_error_label(exc),
            )
        if not events:
            return ensure_valid(
                ok(
                    "Нет событий на ближайшие 7 дней.",
                    intent=intent,
                    mode="tool",
                    actions=_calendar_list_actions([]),
                )
            )
        lines = []
        event_ids: list[str] = []
        event_titles: list[str] = []
        for item in events:
            dt_label = item.start_at.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
            lines.append(f"{item.uid} | {dt_label} | {item.summary}")
            event_ids.append(item.uid)
            event_titles.append(item.summary)
        return ensure_valid(
            ok(
                "\n".join(lines),
                intent=intent,
                mode="tool",
                actions=_calendar_list_actions(event_ids, event_titles),
            )
        )
    return await _list_local_items(start_value, end_value, intent=intent)


async def _list_local_items(
    start: datetime,
    end: datetime,
    *,
    intent: str,
    caldav_error: str | None = None,
) -> OrchestratorResult:
    items = await calendar_store.list_items(start=start, end=end)
    if not items:
        debug = {"calendar_backend": "local_fallback", "caldav_error": caldav_error} if caldav_error else {}
        return ensure_valid(
            ok(
                "Нет событий на ближайшие 7 дней.",
                intent=intent,
                mode="tool",
                debug=debug,
                actions=_calendar_list_actions([]),
            )
        )
    lines = []
    event_ids: list[str] = []
    event_titles: list[str] = []
    for item in items:
        dt_label = item.dt.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{item.id} | {dt_label} | {item.title}")
        event_ids.append(item.id)
        event_titles.append(item.title)
    debug = {"calendar_backend": "local_fallback", "caldav_error": caldav_error} if caldav_error else {}
    return ensure_valid(
        ok(
            "\n".join(lines),
            intent=intent,
            mode="tool",
            debug=debug,
            actions=_calendar_list_actions(event_ids, event_titles),
        )
    )


async def list_reminders(
    now: datetime,
    *,
    limit: int,
    intent: str = "utility_reminders.list",
) -> OrchestratorResult:
    items = await calendar_store.list_reminders(now, limit=limit)
    if not items:
        return ensure_valid(
            ok(
                "Нет запланированных напоминаний.",
                intent=intent,
                mode="tool",
                actions=_reminder_list_actions([], limit),
            )
        )
    lines = []
    actions: list[Action] = _reminder_list_actions(items, limit)
    for item in items:
        when_label = item.trigger_at.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{item.id} | {when_label} | {item.text}")
        actions.extend(_reminder_item_actions(item))
    return ensure_valid(ok("\n".join(lines), intent=intent, mode="tool", actions=actions))


def _calendar_list_actions(event_ids: list[str], event_titles: list[str] | None = None) -> list[Action]:
    actions = [
        Action(id="utility_calendar.add", label="➕ Добавить", payload={"op": "calendar.add"}),
        Action(id="utility_calendar.list", label="🔄 Обновить", payload={"op": "calendar.list"}),
    ]
    for index, event_id in enumerate(event_ids):
        if not isinstance(event_id, str) or not event_id:
            continue
        title = None
        if event_titles and index < len(event_titles):
            title = event_titles[index]
        label = f"🗑 Удалить: {_short_label(title or event_id)}"
        actions.append(
            Action(
                id="utility_calendar.delete",
                label=label,
                payload={"op": "calendar.delete", "event_id": event_id},
            )
        )
    return actions


def _reminder_list_actions(items: list[calendar_store.ReminderItem], limit: int) -> list[Action]:
    actions = [
        Action(id="utility_reminders.create", label="➕ Создать", payload={"op": "reminder.create"}),
        Action(id="utility_reminders.list", label="🔄 Обновить", payload={"op": "reminder.list"}),
        Action(id="menu.open", label="🏠 Меню", payload={"op": "menu_open"}),
    ]
    return actions


def _reminder_item_actions(item: calendar_store.ReminderItem) -> list[Action]:
    actions: list[Action] = []
    label = f"🗑 Удалить: {_short_label(item.text)}"
    actions.append(
        Action(
            id="utility_reminders.delete",
            label=label,
            payload={"op": "reminder.delete", "reminder_id": item.id},
        )
    )
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
    return actions


def _short_label(value: str, limit: int = 24) -> str:
    cleaned = value.strip().replace("\n", " ")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def is_caldav_configured(settings: object | None = None) -> bool:
    if settings is not None:
        url = getattr(settings, "caldav_url", None)
        username = getattr(settings, "caldav_username", None)
        password = getattr(settings, "caldav_password", None)
        return bool(url and username and password)
    return tools_calendar_caldav.load_caldav_config() is not None


async def check_caldav_connection() -> tuple[bool, str | None]:
    config = tools_calendar_caldav.load_caldav_config()
    if config is None:
        return False, None
    return await tools_calendar_caldav.check_connection(config)
