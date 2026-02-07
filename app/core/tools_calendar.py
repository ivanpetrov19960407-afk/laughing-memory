from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from app.core import calendar_store, recurrence_parse, tools_calendar_caldav
from app.core.recurrence_series import (
    build_series,
    delete_instance_this,
    delete_series_future,
    edit_instance_this,
    edit_series_all,
    edit_series_future,
    serialize_overrides,
)
from app.core.calendar_backend import CalendarCreateResult, LocalCalendarBackend
from app.core.error_messages import map_error_text
from app.core.recurrence_scope import RecurrenceScope, normalize_scope
from app.core.result import Action, OrchestratorResult, ensure_valid, error, ok, refused
from app.core.reminders import ReminderScheduler
from app.infra.request_context import RequestContext, add_trace, log_error, log_event
from app.infra.resilience import (
    CircuitBreakerRegistry,
    RetryPolicy,
    TimeoutConfig,
    is_network_error,
    is_timeout_error,
    retry_async,
)

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


def _resolve_timeouts(timeouts: TimeoutConfig | None) -> TimeoutConfig:
    return timeouts or TimeoutConfig()


def _resolve_retry_policy(retry_policy: RetryPolicy | None) -> RetryPolicy:
    return retry_policy or RetryPolicy()


def _is_retryable_calendar_error(exc: Exception) -> bool:
    return is_timeout_error(exc) or is_network_error(exc)


async def create_event(
    *,
    start_at: datetime,
    title: str,
    chat_id: int,
    user_id: int,
    request_id: str | None = None,
    intent: str = "utility_calendar.add",
    reminder_scheduler: ReminderScheduler | None = None,
    reminders_enabled: bool = True,
    request_context: RequestContext | None = None,
    circuit_breakers: CircuitBreakerRegistry | None = None,
    retry_policy: RetryPolicy | None = None,
    timeouts: TimeoutConfig | None = None,
    recurrence_text: str | None = None,
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
    recurrence_input = recurrence_text if isinstance(recurrence_text, str) else title
    recurrence = recurrence_parse.parse_recurrence(recurrence_input, start_at, calendar_store.BOT_TZ)
    rrule = recurrence.rrule if recurrence else None
    exdates = recurrence.exdates if recurrence else None
    recurrence_human = recurrence.human if recurrence else None
    if backend_mode == "caldav":
        timeouts = _resolve_timeouts(timeouts)
        retry_policy = _resolve_retry_policy(retry_policy)
        breaker = circuit_breakers.get("caldav") if circuit_breakers else None
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
                rrule=rrule,
                exdates=exdates,
                recurrence_human=recurrence_human,
                reminder_scheduler=reminder_scheduler,
                reminders_enabled=reminders_enabled,
            )
        if breaker is not None:
            allowed, circuit_event = breaker.allow_request()
            if circuit_event:
                log_event(
                    LOGGER,
                    request_context,
                    component="caldav",
                    event=circuit_event,
                    status="ok",
                    name="calendar.create",
                )
            if not allowed:
                log_event(
                    LOGGER,
                    request_context,
                    component="caldav",
                    event="circuit.short_circuit",
                    status="error",
                    name="calendar.create",
                )
                add_trace(
                    request_context,
                    step="calendar.caldav",
                    component="caldav",
                    name="calendar.create",
                    status="error",
                    duration_ms=0.0,
                )
                return ensure_valid(
                    error(
                        map_error_text("temporarily_unavailable"),
                        intent=intent,
                        mode="tool",
                        debug={"reason": "circuit_open"},
                    )
                )
        start_time = time.monotonic()
        add_trace(
            request_context,
            step="calendar.caldav",
            component="caldav",
            name="calendar.create",
            status="start",
            duration_ms=0.0,
        )
        status = "ok"
        try:
            uid = str(uuid.uuid4())
            created_remote = await retry_async(
                lambda: tools_calendar_caldav.create_event(
                    config,
                    start_at=start_at,
                    end_at=end_at,
                    title=title,
                    uid=uid,
                    rrule=rrule,
                    exdates=exdates,
                ),
                policy=retry_policy,
                timeout_seconds=timeouts.external_api_seconds,
                logger=LOGGER,
                request_context=request_context,
                component="caldav",
                name="calendar.create",
                is_retryable=_is_retryable_calendar_error,
            )
            created_local = await calendar_store.add_item(
                dt=start_at,
                title=title,
                chat_id=chat_id,
                remind_at=None,
                user_id=user_id,
                reminders_enabled=False,
                event_id=created_remote.uid,
                rrule=rrule,
                exdates=exdates,
            )
            event_payload = created_local.get("event") if isinstance(created_local, dict) else None
            event_id = event_payload.get("event_id") if isinstance(event_payload, dict) else None
            if not isinstance(event_id, str) or not event_id:
                raise RuntimeError("local_event_missing_id")
            debug: dict[str, str] = {}
            if created_remote.calendar_name:
                debug["caldav_calendar"] = created_remote.calendar_name
            if created_remote.calendar_url_base:
                debug["caldav_url_base"] = created_remote.calendar_url_base
            if created_remote.uid:
                debug["caldav_uid"] = created_remote.uid
            created = CalendarCreateResult(event_id=event_id, debug=debug)
            await _maybe_schedule_reminder(
                created.event_id,
                reminder_scheduler=reminder_scheduler,
                reminders_enabled=reminders_enabled,
            )
            if breaker is not None:
                circuit_event = breaker.record_success()
                if circuit_event:
                    log_event(
                        LOGGER,
                        request_context,
                        component="caldav",
                        event=circuit_event,
                        status="ok",
                        name="calendar.create",
                    )
            return _build_create_result(
                created,
                start_at=start_at,
                end_at=end_at,
                title=title,
                intent=intent,
                calendar_backend="caldav",
                recurrence_human=recurrence_human,
                rrule=rrule,
                exdates=exdates,
            )
        except asyncio.TimeoutError as exc:
            status = "error"
            if breaker is not None:
                circuit_event = breaker.record_failure()
                if circuit_event:
                    log_event(
                        LOGGER,
                        request_context,
                        component="caldav",
                        event=circuit_event,
                        status="error",
                        name="calendar.create",
                    )
            log_error(
                LOGGER,
                request_context,
                component="caldav",
                where="caldav.timeout",
                exc=exc,
            )
            return ensure_valid(
                error(
                    map_error_text("timeout"),
                    intent=intent,
                    mode="tool",
                    debug={"reason": "timeout"},
                )
            )
        except Exception as exc:
            status = "error"
            if breaker is not None:
                circuit_event = breaker.record_failure()
                if circuit_event:
                    log_event(
                        LOGGER,
                        request_context,
                        component="caldav",
                        event=circuit_event,
                        status="error",
                        name="calendar.create",
                    )
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
                rrule=rrule,
                exdates=exdates,
                recurrence_human=recurrence_human,
                reminder_scheduler=reminder_scheduler,
                reminders_enabled=reminders_enabled,
            )
        finally:
            duration_ms = max((time.monotonic() - start_time) * 1000, 0.01)
            add_trace(
                request_context,
                step="calendar.caldav",
                component="caldav",
                name="calendar.create",
                status=status,
                duration_ms=duration_ms,
            )
    try:
        backend = LocalCalendarBackend(chat_id=chat_id, user_id=user_id, reminders_enabled=False)
        created = await backend.create_event(
            title=title,
            start_dt=start_at,
            end_dt=end_at,
            rrule=rrule,
            exdates=exdates,
        )
    except Exception as exc:
        LOGGER.error(
            "calendar.create local error: request_id=%s user_id=%s error=%s",
            request_label,
            user_id,
            exc.__class__.__name__,
        )
        return ensure_valid(refused("Не удалось создать событие.", intent=intent, mode="tool", debug={"reason": "error"}))
    await _maybe_schedule_reminder(
        created.event_id,
        reminder_scheduler=reminder_scheduler,
        reminders_enabled=reminders_enabled,
    )
    return _build_create_result(
        created,
        start_at=start_at,
        end_at=end_at,
        title=title,
        intent=intent,
        calendar_backend="local",
        recurrence_human=recurrence_human,
        rrule=rrule,
        exdates=exdates,
    )


def _build_create_result(
    created,
    *,
    start_at: datetime,
    end_at: datetime,
    title: str,
    intent: str,
    calendar_backend: str,
    caldav_error: str | None = None,
    recurrence_human: str | None = None,
    rrule: str | None = None,
    exdates: list[datetime] | None = None,
) -> OrchestratorResult:
    event_id = getattr(created, "event_id", None)
    if not isinstance(event_id, str) or not event_id:
        return ensure_valid(refused("Не удалось создать событие.", intent=intent, mode="tool", debug={"reason": "missing_event_id"}))
    start_value = _ensure_aware_for_label(start_at)
    dt_label = start_value.astimezone(calendar_store.BOT_TZ).strftime("%Y-%m-%d %H:%M")
    duration_label = _format_duration(end_at - start_at)
    if recurrence_human:
        text = f"Создал повтор: {recurrence_human}, старт {dt_label}, длительность {duration_label} | {title}"
        if exdates:
            text = f"{text} (исключения: {_format_exdates(exdates)})"
    else:
        text = f"Событие создано: {dt_label} | {title}"
    debug: dict[str, str] = {"event_id": event_id, "calendar_backend": calendar_backend}
    created_debug = getattr(created, "debug", None)
    if isinstance(created_debug, dict):
        for key, value in created_debug.items():
            if isinstance(key, str) and isinstance(value, str):
                debug[key] = value
    if caldav_error:
        debug["caldav_error"] = caldav_error
    if rrule:
        debug["rrule"] = rrule
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
    rrule: str | None,
    exdates: list[datetime] | None,
    recurrence_human: str | None,
    reminder_scheduler: ReminderScheduler | None,
    reminders_enabled: bool,
) -> OrchestratorResult:
    try:
        backend = LocalCalendarBackend(chat_id=chat_id, user_id=user_id, reminders_enabled=False)
        created = await backend.create_event(
            title=title,
            start_dt=start_at,
            end_dt=end_at,
            rrule=rrule,
            exdates=exdates,
        )
    except Exception as exc:
        LOGGER.error(
            "calendar.create local fallback error: user_id=%s error=%s",
            user_id,
            exc.__class__.__name__,
        )
        return ensure_valid(refused("Не удалось создать событие.", intent=intent, mode="tool", debug={"reason": "error"}))
    await _maybe_schedule_reminder(
        created.event_id,
        reminder_scheduler=reminder_scheduler,
        reminders_enabled=reminders_enabled,
    )
    return _build_create_result(
        created,
        start_at=start_at,
        end_at=end_at,
        title=title,
        intent=intent,
        calendar_backend="local_fallback",
        caldav_error=caldav_error,
        recurrence_human=recurrence_human,
        rrule=rrule,
        exdates=exdates,
    )


async def _maybe_schedule_reminder(
    event_id: str,
    *,
    reminder_scheduler: ReminderScheduler | None,
    reminders_enabled: bool,
) -> None:
    if not reminders_enabled:
        return
    if not isinstance(event_id, str) or not event_id:
        return
    event = await calendar_store.get_event(event_id)
    if event is None:
        return
    reminder = await calendar_store.ensure_reminder_for_event(event, trigger_at=event.dt, enabled=True)
    if reminder_scheduler is not None:
        await reminder_scheduler.schedule_reminder(reminder)


async def delete_event(
    item_id: str,
    *,
    scope: RecurrenceScope | str | None = None,
    instance_dt: datetime | None = None,
    user_id: int,
    intent: str = "utility_calendar.delete",
    request_context: RequestContext | None = None,
    circuit_breakers: CircuitBreakerRegistry | None = None,
    retry_policy: RetryPolicy | None = None,
    timeouts: TimeoutConfig | None = None,
) -> OrchestratorResult:
    backend_mode = _resolve_backend_mode()
    deleted_remote = False
    caldav_error: str | None = None
    scope_value = normalize_scope(scope) or RecurrenceScope.ALL
    event = await calendar_store.get_event(item_id)
    is_recurring = event is not None and bool(event.rrule)
    if is_recurring and scope is None:
        return ensure_valid(
            refused(
                "Нужно выбрать область изменения для повторяющегося события.",
                intent=intent,
                mode="tool",
                debug={"reason": "missing_scope"},
            )
        )
    if is_recurring and scope_value in {RecurrenceScope.THIS, RecurrenceScope.FUTURE} and instance_dt is None:
        instance_dt = event.dt if event else None
    if scope_value in {RecurrenceScope.THIS, RecurrenceScope.FUTURE} and instance_dt is None:
        return ensure_valid(
            refused(
                "Не удалось определить дату инстанса.",
                intent=intent,
                mode="tool",
                debug={"reason": "missing_instance_dt"},
            )
        )
    if backend_mode == "caldav":
        timeouts = _resolve_timeouts(timeouts)
        retry_policy = _resolve_retry_policy(retry_policy)
        breaker = circuit_breakers.get("caldav") if circuit_breakers else None
        config = tools_calendar_caldav.load_caldav_config()
        if config is None:
            caldav_error = "missing_config"
            LOGGER.info("calendar.delete fallback: user_id=%s reason=caldav_missing_config", user_id)
        else:
            start_time = time.monotonic()
            status = "ok"
            add_trace(
                request_context,
                step="calendar.caldav",
                component="caldav",
                name="calendar.delete",
                status="start",
                duration_ms=0.0,
            )
            try:
                if breaker is not None:
                    allowed, circuit_event = breaker.allow_request()
                    if circuit_event:
                        log_event(
                            LOGGER,
                            request_context,
                            component="caldav",
                            event=circuit_event,
                            status="ok",
                            name="calendar.delete",
                        )
                    if not allowed:
                        log_event(
                            LOGGER,
                            request_context,
                            component="caldav",
                            event="circuit.short_circuit",
                            status="error",
                            name="calendar.delete",
                        )
                        status = "error"
                        add_trace(
                            request_context,
                            step="calendar.caldav",
                            component="caldav",
                            name="calendar.delete",
                            status="error",
                            duration_ms=0.0,
                        )
                        return ensure_valid(
                            error(
                                map_error_text("temporarily_unavailable"),
                                intent=intent,
                                mode="tool",
                                debug={"reason": "circuit_open"},
                            )
                        )
                if scope_value == RecurrenceScope.ALL:
                    deleted_remote = await retry_async(
                        lambda: tools_calendar_caldav.delete_event(config, event_id=item_id),
                        policy=retry_policy,
                        timeout_seconds=timeouts.external_api_seconds,
                        logger=LOGGER,
                        request_context=request_context,
                        component="caldav",
                        name="calendar.delete",
                        is_retryable=_is_retryable_calendar_error,
                    )
                else:
                    if event is None:
                        return ensure_valid(
                            refused("Событие не найдено.", intent=intent, mode="tool", debug={"reason": "missing_event"})
                        )
                    updated_rrule, updated_exdates = _apply_recurrence_delete_scope(
                        event,
                        scope_value,
                        instance_dt=instance_dt,
                    )
                    await retry_async(
                        lambda: tools_calendar_caldav.update_event(
                            config,
                            event_id=item_id,
                            start_at=event.dt,
                            end_at=event.dt + timedelta(hours=1),
                            title=event.title,
                            rrule=updated_rrule,
                            exdates=updated_exdates,
                        ),
                        policy=retry_policy,
                        timeout_seconds=timeouts.external_api_seconds,
                        logger=LOGGER,
                        request_context=request_context,
                        component="caldav",
                        name="calendar.delete",
                        is_retryable=_is_retryable_calendar_error,
                    )
                    deleted_remote = True
                if breaker is not None:
                    circuit_event = breaker.record_success()
                    if circuit_event:
                        log_event(
                            LOGGER,
                            request_context,
                            component="caldav",
                            event=circuit_event,
                            status="ok",
                            name="calendar.delete",
                        )
            except asyncio.TimeoutError as exc:
                status = "error"
                if breaker is not None:
                    circuit_event = breaker.record_failure()
                    if circuit_event:
                        log_event(
                            LOGGER,
                            request_context,
                            component="caldav",
                            event=circuit_event,
                            status="error",
                            name="calendar.delete",
                        )
                log_error(
                    LOGGER,
                    request_context,
                    component="caldav",
                    where="caldav.timeout",
                    exc=exc,
                )
                return ensure_valid(
                    error(
                        map_error_text("timeout"),
                        intent=intent,
                        mode="tool",
                        debug={"reason": "timeout"},
                    )
                )
            except Exception as exc:
                status = "error"
                if breaker is not None:
                    circuit_event = breaker.record_failure()
                    if circuit_event:
                        log_event(
                            LOGGER,
                            request_context,
                            component="caldav",
                            event=circuit_event,
                            status="error",
                            name="calendar.delete",
                        )
                caldav_error = _safe_caldav_error_label(exc)
                LOGGER.error("calendar.delete caldav error: user_id=%s error=%s", user_id, exc.__class__.__name__)
            finally:
                duration_ms = max((time.monotonic() - start_time) * 1000, 0.01)
                add_trace(
                    request_context,
                    step="calendar.caldav",
                    component="caldav",
                    name="calendar.delete",
                    status=status,
                    duration_ms=duration_ms,
                )
    removed = False
    reminder_id: str | None = None
    if scope_value == RecurrenceScope.ALL or not is_recurring:
        removed, reminder_id = await calendar_store.delete_item(item_id)
    else:
        if event is None:
            return ensure_valid(refused("Событие не найдено.", intent=intent, mode="tool"))
        series = build_series(event)
        if scope_value == RecurrenceScope.THIS:
            if instance_dt is None:
                return ensure_valid(refused("Не удалось определить дату инстанса.", intent=intent, mode="tool"))
            updated_series = delete_instance_this(series, instance_dt)
        else:
            if instance_dt is None:
                return ensure_valid(refused("Не удалось определить дату инстанса.", intent=intent, mode="tool"))
            updated_series = delete_series_future(series, instance_dt)
        updated_event, reminder_id = await calendar_store.update_event_fields(
            item_id,
            new_rrule=updated_series.rrule,
            new_exdates=updated_series.exdates,
            new_overrides=serialize_overrides(updated_series.overrides),
            new_timezone=updated_series.timezone.key,
            new_series_id=updated_series.series_id,
        )
        removed = updated_event is not None
    deleted = deleted_remote or removed
    text = f"Удалено: {item_id}" if deleted else f"Не найдено: {item_id}"
    debug: dict[str, object] = {}
    debug["event_id"] = item_id
    if reminder_id:
        debug["reminder_id"] = reminder_id
    if caldav_error:
        debug["calendar_backend"] = "local_fallback"
        debug["caldav_error"] = caldav_error
    result = ok(text, intent=intent, mode="tool", debug=debug) if deleted else refused(text, intent=intent, mode="tool", debug=debug)
    return ensure_valid(result)


async def update_event(
    event_id: str,
    changes: dict[str, object],
    *,
    scope: RecurrenceScope | str | None = None,
    instance_dt: datetime | None = None,
    user_id: int,
    chat_id: int,
    intent: str = "utility_calendar.update",
    request_context: RequestContext | None = None,
    circuit_breakers: CircuitBreakerRegistry | None = None,
    retry_policy: RetryPolicy | None = None,
    timeouts: TimeoutConfig | None = None,
) -> OrchestratorResult:
    backend_mode = _resolve_backend_mode()
    scope_value = normalize_scope(scope) or RecurrenceScope.ALL
    event = await calendar_store.get_event(event_id)
    if event is None:
        return ensure_valid(refused("Событие не найдено.", intent=intent, mode="tool"))
    is_recurring = bool(event.rrule)
    if is_recurring and scope is None:
        return ensure_valid(
            refused(
                "Нужно выбрать область изменения для повторяющегося события.",
                intent=intent,
                mode="tool",
                debug={"reason": "missing_scope"},
            )
        )
    if is_recurring and scope_value in {RecurrenceScope.THIS, RecurrenceScope.FUTURE} and instance_dt is None:
        instance_dt = event.dt
    if scope_value in {RecurrenceScope.THIS, RecurrenceScope.FUTURE} and instance_dt is None:
        return ensure_valid(
            refused(
                "Не удалось определить дату инстанса.",
                intent=intent,
                mode="tool",
                debug={"reason": "missing_instance_dt"},
            )
        )
    updated_local, reminder_id = await _apply_local_update_scope(
        event,
        changes,
        scope=scope_value,
        instance_dt=instance_dt,
        chat_id=chat_id,
        user_id=user_id,
    )
    caldav_error: str | None = None
    if backend_mode == "caldav":
        timeouts = _resolve_timeouts(timeouts)
        retry_policy = _resolve_retry_policy(retry_policy)
        breaker = circuit_breakers.get("caldav") if circuit_breakers else None
        config = tools_calendar_caldav.load_caldav_config()
        if config is None:
            caldav_error = "missing_config"
        else:
            if breaker is not None:
                allowed, circuit_event = breaker.allow_request()
                if circuit_event:
                    log_event(
                        LOGGER,
                        request_context,
                        component="caldav",
                        event=circuit_event,
                        status="ok",
                        name="calendar.update",
                    )
                if not allowed:
                    return ensure_valid(
                        error(
                            map_error_text("temporarily_unavailable"),
                            intent=intent,
                            mode="tool",
                            debug={"reason": "circuit_open"},
                        )
                    )
            try:
                await _apply_caldav_update_scope(
                    config,
                    event,
                    changes,
                    scope=scope_value,
                    instance_dt=instance_dt,
                    policy=retry_policy,
                    timeouts=timeouts,
                    request_context=request_context,
                )
                if breaker is not None:
                    circuit_event = breaker.record_success()
                    if circuit_event:
                        log_event(
                            LOGGER,
                            request_context,
                            component="caldav",
                            event=circuit_event,
                            status="ok",
                            name="calendar.update",
                        )
            except Exception as exc:
                if breaker is not None:
                    circuit_event = breaker.record_failure()
                    if circuit_event:
                        log_event(
                            LOGGER,
                            request_context,
                            component="caldav",
                            event=circuit_event,
                            status="error",
                            name="calendar.update",
                        )
                caldav_error = _safe_caldav_error_label(exc)
    if updated_local is None:
        return ensure_valid(refused("Событие не найдено.", intent=intent, mode="tool"))
    debug: dict[str, object] = {"event_id": event_id}
    if reminder_id:
        debug["reminder_id"] = reminder_id
    if caldav_error:
        debug["calendar_backend"] = "local_fallback"
        debug["caldav_error"] = caldav_error
    return ensure_valid(ok("Событие обновлено.", intent=intent, mode="tool", debug=debug))


async def _apply_caldav_update_scope(
    config: tools_calendar_caldav.CalDAVConfig,
    event: calendar_store.CalendarItem,
    changes: dict[str, object],
    *,
    scope: RecurrenceScope,
    instance_dt: datetime | None,
    policy: RetryPolicy,
    timeouts: TimeoutConfig,
    request_context: RequestContext | None,
) -> None:
    updated_start = _resolve_change_datetime(changes.get("start_at")) or event.dt
    updated_title = _resolve_change_text(changes.get("title")) or event.title
    updated_end = updated_start + timedelta(hours=1)
    if scope == RecurrenceScope.ALL or not event.rrule:
        await retry_async(
            lambda: tools_calendar_caldav.update_event(
                config,
                event_id=event.id,
                start_at=updated_start,
                end_at=updated_end,
                title=updated_title,
                rrule=event.rrule,
                exdates=event.exdates,
            ),
            policy=policy,
            timeout_seconds=timeouts.external_api_seconds,
            logger=LOGGER,
            request_context=request_context,
            component="caldav",
            name="calendar.update",
            is_retryable=_is_retryable_calendar_error,
        )
        return
    if scope == RecurrenceScope.THIS:
        updated_rrule, updated_exdates = _apply_recurrence_delete_scope(
            event,
            scope,
            instance_dt=instance_dt,
        )
        await retry_async(
            lambda: tools_calendar_caldav.update_event(
                config,
                event_id=event.id,
                start_at=event.dt,
                end_at=event.dt + timedelta(hours=1),
                title=event.title,
                rrule=updated_rrule,
                exdates=updated_exdates,
            ),
            policy=policy,
            timeout_seconds=timeouts.external_api_seconds,
            logger=LOGGER,
            request_context=request_context,
            component="caldav",
            name="calendar.update",
            is_retryable=_is_retryable_calendar_error,
        )
        await retry_async(
            lambda: tools_calendar_caldav.create_event(
                config,
                start_at=updated_start,
                end_at=updated_end,
                title=updated_title,
            ),
            policy=policy,
            timeout_seconds=timeouts.external_api_seconds,
            logger=LOGGER,
            request_context=request_context,
            component="caldav",
            name="calendar.update",
            is_retryable=_is_retryable_calendar_error,
        )
        return
    if scope == RecurrenceScope.FUTURE:
        updated_rrule, _ = _apply_recurrence_delete_scope(
            event,
            scope,
            instance_dt=instance_dt,
        )
        await retry_async(
            lambda: tools_calendar_caldav.update_event(
                config,
                event_id=event.id,
                start_at=event.dt,
                end_at=event.dt + timedelta(hours=1),
                title=event.title,
                rrule=updated_rrule,
                exdates=event.exdates,
            ),
            policy=policy,
            timeout_seconds=timeouts.external_api_seconds,
            logger=LOGGER,
            request_context=request_context,
            component="caldav",
            name="calendar.update",
            is_retryable=_is_retryable_calendar_error,
        )
        new_rrule = _strip_rrule_parts(event.rrule, {"UNTIL", "COUNT"})
        await retry_async(
            lambda: tools_calendar_caldav.create_event(
                config,
                start_at=updated_start,
                end_at=updated_end,
                title=updated_title,
                rrule=new_rrule,
            ),
            policy=policy,
            timeout_seconds=timeouts.external_api_seconds,
            logger=LOGGER,
            request_context=request_context,
            component="caldav",
            name="calendar.update",
            is_retryable=_is_retryable_calendar_error,
        )


async def _apply_local_update_scope(
    event: calendar_store.CalendarItem,
    changes: dict[str, object],
    *,
    scope: RecurrenceScope,
    instance_dt: datetime | None,
    chat_id: int,
    user_id: int,
) -> tuple[calendar_store.CalendarItem | None, str | None]:
    updated_start = _resolve_change_datetime(changes.get("start_at")) or event.dt
    updated_title = _resolve_change_text(changes.get("title")) or event.title
    if scope == RecurrenceScope.ALL or not event.rrule:
        series = build_series(event)
        updated_series = edit_series_all(
            series,
            {"start_at": updated_start, "title": updated_title, "rrule": changes.get("rrule")},
        )
        updated_event, reminder_id = await calendar_store.update_event_fields(
            event.id,
            new_dt=updated_series.start_dt,
            new_title=updated_series.title,
            new_rrule=updated_series.rrule,
            new_exdates=updated_series.exdates,
            new_overrides=serialize_overrides(updated_series.overrides),
            new_timezone=updated_series.timezone.key,
            new_series_id=updated_series.series_id,
        )
        return updated_event, reminder_id
    if scope == RecurrenceScope.THIS:
        if instance_dt is None:
            return None, None
        series = build_series(event)
        updated_series = edit_instance_this(
            series,
            instance_dt,
            {"start_at": updated_start, "title": updated_title},
        )
        updated_event, reminder_id = await calendar_store.update_event_fields(
            event.id,
            new_rrule=updated_series.rrule,
            new_exdates=updated_series.exdates,
            new_overrides=serialize_overrides(updated_series.overrides),
            new_timezone=updated_series.timezone.key,
            new_series_id=updated_series.series_id,
        )
        return updated_event, reminder_id
    if scope == RecurrenceScope.FUTURE:
        if instance_dt is None:
            return None, None
        series = build_series(event)
        master_series, future_series = edit_series_future(
            series,
            instance_dt,
            {"start_at": updated_start, "title": updated_title},
        )
        await calendar_store.update_event_fields(
            event.id,
            new_rrule=master_series.rrule,
            new_exdates=master_series.exdates,
            new_overrides=serialize_overrides(master_series.overrides),
            new_timezone=master_series.timezone.key,
            new_series_id=master_series.series_id,
        )
        created = await calendar_store.add_item(
            dt=future_series.start_dt,
            title=future_series.title,
            chat_id=chat_id,
            remind_at=None,
            user_id=user_id,
            reminders_enabled=False,
            rrule=future_series.rrule,
            exdates=future_series.exdates,
            overrides=serialize_overrides(future_series.overrides),
            series_id=None,
            timezone=future_series.timezone.key,
        )
        created_event = created.get("event") if isinstance(created, dict) else None
        created_id = created_event.get("event_id") if isinstance(created_event, dict) else None
        event_value = await calendar_store.get_event(created_id) if isinstance(created_id, str) else None
        return event_value, None
    return None, None


def _apply_recurrence_delete_scope(
    event: calendar_store.CalendarItem,
    scope: RecurrenceScope,
    *,
    instance_dt: datetime | None,
) -> tuple[str | None, list[datetime] | None]:
    rrule = event.rrule
    if not rrule:
        return rrule, event.exdates
    if scope == RecurrenceScope.THIS:
        exdates = list(event.exdates or [])
        if instance_dt is not None:
            exdates.append(_ensure_aware_for_label(instance_dt))
        return rrule, _dedupe_exdates(exdates)
    if scope == RecurrenceScope.FUTURE:
        base_dt = instance_dt or event.dt
        until_value = _format_utc_value(base_dt - timedelta(seconds=1))
        updated_rrule = _set_rrule_part(rrule, "UNTIL", until_value)
        return updated_rrule, event.exdates
    return rrule, event.exdates


def _resolve_change_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return None


def _resolve_change_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _dedupe_exdates(exdates: list[datetime]) -> list[datetime] | None:
    seen: set[str] = set()
    result: list[datetime] = []
    for item in exdates:
        if not isinstance(item, datetime):
            continue
        iso = item.astimezone(calendar_store.BOT_TZ).isoformat()
        if iso in seen:
            continue
        seen.add(iso)
        result.append(item)
    return result or None


def _parse_rrule(rrule: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for segment in rrule.split(";"):
        if "=" not in segment:
            continue
        key, value = segment.split("=", 1)
        if key and value:
            parts[key] = value
    return parts


def _format_rrule(parts: dict[str, str]) -> str:
    return ";".join(f"{key}={value}" for key, value in parts.items())


def _set_rrule_part(rrule: str, key: str, value: str) -> str:
    parts = _parse_rrule(rrule)
    parts[key] = value
    return _format_rrule(parts)


def _strip_rrule_parts(rrule: str | None, keys: set[str]) -> str | None:
    if not rrule:
        return rrule
    parts = _parse_rrule(rrule)
    for key in keys:
        parts.pop(key, None)
    if not parts:
        return None
    return _format_rrule(parts)


def _format_utc_value(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def list_calendar_items(
    start: datetime | None,
    end: datetime | None,
    *,
    user_id: int,
    intent: str = "utility_calendar.list",
    request_context: RequestContext | None = None,
    circuit_breakers: CircuitBreakerRegistry | None = None,
    retry_policy: RetryPolicy | None = None,
    timeouts: TimeoutConfig | None = None,
) -> OrchestratorResult:
    backend_mode = _resolve_backend_mode()
    start_value = start or datetime.now(tz=calendar_store.BOT_TZ)
    end_value = end or (start_value + timedelta(days=7))
    if backend_mode == "caldav":
        timeouts = _resolve_timeouts(timeouts)
        retry_policy = _resolve_retry_policy(retry_policy)
        breaker = circuit_breakers.get("caldav") if circuit_breakers else None
        config = tools_calendar_caldav.load_caldav_config()
        if config is None:
            LOGGER.info("calendar.list fallback: user_id=%s reason=caldav_missing_config", user_id)
            return await _list_local_items(
                start_value,
                end_value,
                intent=intent,
                caldav_error="missing_config",
            )
        start_time = time.monotonic()
        status = "ok"
        add_trace(
            request_context,
            step="calendar.caldav",
            component="caldav",
            name="calendar.list",
            status="start",
            duration_ms=0.0,
        )
        try:
            if breaker is not None:
                allowed, circuit_event = breaker.allow_request()
                if circuit_event:
                    log_event(
                        LOGGER,
                        request_context,
                        component="caldav",
                        event=circuit_event,
                        status="ok",
                        name="calendar.list",
                    )
                if not allowed:
                    log_event(
                        LOGGER,
                        request_context,
                        component="caldav",
                        event="circuit.short_circuit",
                        status="error",
                        name="calendar.list",
                    )
                    status = "error"
                    return ensure_valid(
                        error(
                            map_error_text("temporarily_unavailable"),
                            intent=intent,
                            mode="tool",
                            debug={"reason": "circuit_open"},
                        )
                    )
            events = await retry_async(
                lambda: tools_calendar_caldav.list_events(config, start=start_value, end=end_value, limit=20),
                policy=retry_policy,
                timeout_seconds=timeouts.external_api_seconds,
                logger=LOGGER,
                request_context=request_context,
                component="caldav",
                name="calendar.list",
                is_retryable=_is_retryable_calendar_error,
            )
            if breaker is not None:
                circuit_event = breaker.record_success()
                if circuit_event:
                    log_event(
                        LOGGER,
                        request_context,
                        component="caldav",
                        event=circuit_event,
                        status="ok",
                        name="calendar.list",
                    )
        except asyncio.TimeoutError as exc:
            status = "error"
            if breaker is not None:
                circuit_event = breaker.record_failure()
                if circuit_event:
                    log_event(
                        LOGGER,
                        request_context,
                        component="caldav",
                        event=circuit_event,
                        status="error",
                        name="calendar.list",
                    )
            log_error(
                LOGGER,
                request_context,
                component="caldav",
                where="caldav.timeout",
                exc=exc,
            )
            return ensure_valid(
                error(
                    map_error_text("timeout"),
                    intent=intent,
                    mode="tool",
                    debug={"reason": "timeout"},
                )
            )
        except Exception as exc:
            status = "error"
            if breaker is not None:
                circuit_event = breaker.record_failure()
                if circuit_event:
                    log_event(
                        LOGGER,
                        request_context,
                        component="caldav",
                        event=circuit_event,
                        status="error",
                        name="calendar.list",
                    )
            LOGGER.error("calendar.list caldav error: user_id=%s error=%s", user_id, exc.__class__.__name__)
            return await _list_local_items(
                start_value,
                end_value,
                intent=intent,
                caldav_error=_safe_caldav_error_label(exc),
            )
        finally:
            duration_ms = max((time.monotonic() - start_time) * 1000, 0.01)
            add_trace(
                request_context,
                step="calendar.caldav",
                component="caldav",
                name="calendar.list",
                status=status,
                duration_ms=duration_ms,
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
        Action(id="utility_reminders.list", label="🔄 Обновить", payload={"op": "reminder.list", "limit": limit}),
        Action(id="menu.open", label="🏠 Меню", payload={"op": "menu_open"}),
    ]
    return actions


def _reminder_item_actions(item: calendar_store.ReminderItem) -> list[Action]:
    label = f"🗑 Удалить: {_short_label(item.text)}"
    return [
        Action(
            id="utility_reminders.delete",
            label=label,
            payload={"op": "reminder.delete_confirm", "reminder_id": item.id},
        ),
        Action(
            id=f"reminder_reschedule:{item.id}",
            label="✏ Перенести",
            payload={"op": "reminder_reschedule", "reminder_id": item.id, "base_trigger_at": item.trigger_at.isoformat()},
        ),
    ]


def _short_label(value: str, limit: int = 24) -> str:
    cleaned = value.strip().replace("\n", " ")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _format_duration(value: timedelta) -> str:
    total_seconds = int(value.total_seconds())
    if total_seconds < 0:
        total_seconds = 0
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"{hours}:{minutes:02d}"


def _format_exdates(exdates: list[datetime]) -> str:
    labels = []
    for item in exdates:
        if not isinstance(item, datetime):
            continue
        labels.append(item.astimezone(calendar_store.BOT_TZ).strftime("%d.%m.%Y"))
    return ", ".join(labels)


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
