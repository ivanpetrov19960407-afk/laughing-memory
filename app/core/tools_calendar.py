from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.core import calendar_store
from app.core.result import Action, OrchestratorResult, ensure_valid, ok, refused
from app.infra.google_oauth import load_google_oauth_config, refresh_access_token
from app.stores.google_tokens import GoogleTokenStore, GoogleTokens

LOGGER = logging.getLogger(__name__)

_NOT_CONNECTED_TEXT = (
    "Календарь не подключён. Нужно авторизоваться/подключить Google Calendar в настройках."
)


def _get_token_store() -> GoogleTokenStore:
    return GoogleTokenStore.from_env()


async def _create_google_event(
    *,
    access_token: str,
    start_at: datetime,
    title: str,
) -> dict[str, object]:
    tz = ZoneInfo("Europe/Vilnius")
    start_local = start_at.astimezone(tz)
    end_local = start_local + timedelta(hours=1)
    payload = {
        "summary": title,
        "start": {"dateTime": start_local.isoformat(), "timeZone": "Europe/Vilnius"},
        "end": {"dateTime": end_local.isoformat(), "timeZone": "Europe/Vilnius"},
    }
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


def _refresh_tokens_if_needed(
    *,
    config,
    tokens: GoogleTokens,
    user_id: int,
    token_store: GoogleTokenStore,
    force: bool = False,
) -> GoogleTokens:
    if not tokens.access_token:
        force = True
    if not force and not tokens.is_expired(now=time.time()):
        return tokens
    refreshed = refresh_access_token(config, refresh_token=tokens.refresh_token)
    access_token = refreshed.get("access_token")
    expires_in = refreshed.get("expires_in")
    if not isinstance(access_token, str):
        raise RuntimeError("refresh_missing_access_token")
    expires_at = time.time() + float(expires_in) if isinstance(expires_in, (int, float)) else None
    token_store.update_access_token(
        user_id,
        access_token=access_token,
        expires_at=expires_at,
        scope=refreshed.get("scope") if isinstance(refreshed.get("scope"), str) else None,
        token_type=refreshed.get("token_type") if isinstance(refreshed.get("token_type"), str) else None,
    )
    return GoogleTokens(
        access_token=access_token,
        refresh_token=tokens.refresh_token,
        expires_at=expires_at,
        token_type=tokens.token_type,
        scope=tokens.scope,
    )


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
    token_store = _get_token_store()
    tokens = token_store.get_tokens(user_id)
    if tokens is None:
        LOGGER.info(
            "calendar.create refused: request_id=%s user_id=%s reason=calendar_not_connected",
            request_label,
            user_id,
        )
        return ensure_valid(
            refused(
                _NOT_CONNECTED_TEXT,
                intent=intent,
                mode="tool",
                debug={"reason": "calendar_not_connected"},
            )
        )
    config = load_google_oauth_config()
    if config is None:
        LOGGER.error("calendar.create error: request_id=%s user_id=%s reason=oauth_not_configured", request_label, user_id)
        return ensure_valid(refused("OAuth для Google Calendar не настроен.", intent=intent, mode="tool"))
    try:
        tokens = _refresh_tokens_if_needed(config=config, tokens=tokens, user_id=user_id, token_store=token_store)
        try:
            event_payload = await _create_google_event(
                access_token=tokens.access_token,
                start_at=start_at,
                title=title,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                tokens = _refresh_tokens_if_needed(
                    config=config,
                    tokens=tokens,
                    user_id=user_id,
                    token_store=token_store,
                    force=True,
                )
                event_payload = await _create_google_event(
                    access_token=tokens.access_token,
                    start_at=start_at,
                    title=title,
                )
            else:
                raise
        event_id = event_payload.get("id") if isinstance(event_payload, dict) else None
        if not isinstance(event_id, str):
            raise RuntimeError("missing_event_id")
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
    user_id: int,
    intent: str = "utility_calendar.del",
) -> OrchestratorResult:
    token_store = _get_token_store()
    tokens = token_store.get_tokens(user_id)
    if tokens is None:
        LOGGER.info("calendar.delete refused: reason=calendar_not_connected user_id=%s", user_id)
        return ensure_valid(
            refused(_NOT_CONNECTED_TEXT, intent=intent, mode="tool", debug={"reason": "calendar_not_connected"})
        )
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
    user_id: int,
    intent: str = "utility_calendar.list",
) -> OrchestratorResult:
    token_store = _get_token_store()
    tokens = token_store.get_tokens(user_id)
    if tokens is None:
        LOGGER.info("calendar.list refused: reason=calendar_not_connected user_id=%s", user_id)
        return ensure_valid(
            refused(_NOT_CONNECTED_TEXT, intent=intent, mode="tool", debug={"reason": "calendar_not_connected"})
        )
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
