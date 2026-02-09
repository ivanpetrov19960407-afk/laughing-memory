"""Orchestrator: routes user input to tasks/LLM/tools and returns OrchestratorResult.

Business logic lives here and in Tools; Telegram layer only displays result and
builds inline buttons from result.actions. Contract: handlers call orchestrator,
get OrchestratorResult (text/status/mode/intent/sources/actions/attachments/debug).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
import re
import traceback
from typing import Any

from app.core.decision import Decision
from app.core.error_messages import map_error_text
from app.core.models import TaskExecutionResult
from app.core.facts import build_sources_prompt, render_fact_response_with_sources
from app.core.result import (
    OrchestratorResult,
    Source,
    ensure_safe_text_strict,
    ensure_valid,
    error,
    ok,
    refused,
)
from app.core.text_safety import SAFE_FALLBACK_TEXT, SOURCES_DISCLAIMER_TEXT, is_sources_request, sanitize_llm_text
from app.core.tasks import TaskDefinition, TaskError, get_task_registry
from app.infra.access import AccessController
from app.infra.llm import LLMAPIError, LLMClient, LLMGuardError, ensure_plain_text
from app.infra.rate_limit import RateLimiter
from app.infra.resilience import (
    CircuitBreakerRegistry,
    RetryPolicy,
    TimeoutConfig,
    is_network_error,
    is_timeout_error,
    load_circuit_breaker_config,
    load_retry_policy,
    load_timeouts,
    retry_async,
)
from app.infra.request_context import (
    RequestContext,
    add_trace,
    build_args_shape,
    elapsed_ms,
    log_error,
    log_event,
)
from app.infra.storage import TaskStorage
from app.tools.web_search import NullSearchClient, SearchClient


LOGGER = logging.getLogger(__name__)
_PLAIN_TEXT_SYSTEM_PROMPT = (
    "Ответь только текстом. Не возвращай JSON, поля, статус, intent, sources, actions.\n"
    "Запрещено использовать ссылки, цитаты, номера источников, квадратные/круглые скобки "
    "с цифрами (например [1], [2], (1)), фразы \"по данным\", \"согласно\", \"источник:\", "
    "\"references\". Если источники не были предоставлены отдельно, не упоминай источники вовсе. "
    "Верни только связный текст без упоминаний источников."
)
_UNKNOWN_COMMAND_MESSAGE = "Неизвестная команда. Открой /menu."
_DESTRUCTIVE_REFUSAL = "Не могу выполнить разрушительное действие."


def detect_intent(text: str) -> str:
    """Classify raw text into intent namespace (e.g. smalltalk.local, question.general)."""
    trimmed = text.strip()
    if not trimmed:
        return "intent.unknown"
    lowered = trimmed.lower()
    if lowered.startswith("summary:") or lowered.startswith("/summary"):
        return "utility.summary"
    if trimmed.startswith("/"):
        return "command.raw"
    smalltalk_markers = (
        "привет",
        "здравств",
        "как дела",
        "спасибо",
        "пока",
        "hello",
        "hi",
        "hey",
        "thanks",
        "thank you",
        "bye",
        "goodbye",
    )
    if any(marker in lowered for marker in smalltalk_markers):
        return "smalltalk.local"
    return "question.general"


def _tool_debug_payload(
    request_context: RequestContext | None,
    exc: Exception,
    *,
    reason: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"reason": reason}
    if request_context and request_context.env == "dev":
        payload["error_type"] = type(exc).__name__
        payload["error_message"] = str(exc)
        payload["stacktrace"] = traceback.format_exc()
    return payload


def _llm_error_payload(request_context: RequestContext | None, exc: Exception) -> dict[str, Any]:
    payload: dict[str, Any] = {"error_type": type(exc).__name__}
    if request_context and request_context.env == "dev":
        payload["error_message"] = str(exc)
        payload["stacktrace"] = traceback.format_exc()
    return payload


class TaskNotFoundError(TaskError):
    """Raised when task name is missing from registry."""


class TaskDisabledError(TaskError):
    """Raised when task is disabled by config."""


class Orchestrator:
    """Routes user input to tasks, LLM, or tools; returns OrchestratorResult."""

    _MAX_INPUT_LENGTH = 5500

    def __init__(
        self,
        config: dict[str, Any],
        storage: TaskStorage,
        registry: dict[str, TaskDefinition] | None = None,
        llm_client: LLMClient | None = None,
        access: AccessController | None = None,
        rate_limiter: RateLimiter | None = None,
        llm_history_turns: int | None = None,
        llm_model: str | None = None,
        search_client: SearchClient | None = None,
        feature_web_search: bool = True,
        timeouts: TimeoutConfig | None = None,
        retry_policy: RetryPolicy | None = None,
        circuit_breakers: CircuitBreakerRegistry | None = None,
    ) -> None:
        self._config = config
        self._storage = storage
        self._registry = registry or get_task_registry()
        self._llm_client = llm_client
        self._access = access
        self._rate_limiter = rate_limiter
        self._llm_history_turns = llm_history_turns
        self._llm_model = llm_model
        self._search_client = search_client or NullSearchClient()
        self._feature_web_search = feature_web_search
        self._facts_only_default = _coerce_bool(config.get("facts_only_default", False))
        self._facts_only_by_user: dict[int, bool] = {}
        self._timeouts = timeouts or load_timeouts(config)
        self._retry_policy = retry_policy or load_retry_policy(config)
        self._circuit_breakers = circuit_breakers or CircuitBreakerRegistry(
            config=load_circuit_breaker_config(config),
        )

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    def list_tasks(self) -> list[TaskDefinition]:
        enabled = self._enabled_tasks()
        return [self._registry[name] for name in enabled if name in self._registry]

    async def handle(
        self,
        text: str,
        user_context: dict[str, Any],
        *,
        request_context: RequestContext | None = None,
    ) -> OrchestratorResult:
        user_id = int(user_context.get("user_id") or 0)
        dialog_context = user_context.get("dialog_context")
        dialog_message_count = user_context.get("dialog_message_count")
        memory_context = user_context.get("memory_context")
        request_id = user_context.get("request_id")
        request_context = request_context or user_context.get("request_context")
        start_time = time.monotonic()
        log_event(
            LOGGER,
            request_context,
            component="orchestrator",
            event="orchestrator.start",
            status="ok",
            user_id=user_id,
            input_text=text,
        )
        allowed, error_message = self._ensure_allowed(user_id)
        if not allowed:
            result = ensure_valid(
                refused(
                    error_message,
                    intent="command.access_denied",
                    mode="local",
                    debug={"reason": "access_denied"},
                )
            )
            return self._finalize_request(request_context, start_time, result)
        trimmed = text.strip()
        decision = self._make_decision(trimmed)
        LOGGER.info(
            "Decision: user_id=%s intent=%s status=%s reason=%s",
            user_id,
            decision.intent,
            decision.status,
            decision.reason or "-",
        )
        if decision.status != "ok":
            result = ensure_valid(self._result_from_decision(decision))
            return self._finalize_request(request_context, start_time, result)

        if decision.intent == "smalltalk.local":
            response = self._smalltalk_response(trimmed)
            result = ensure_valid(
                ok(
                    response,
                    intent=decision.intent,
                    mode="local",
                    debug={"strategy": "smalltalk_local"},
                )
            )
            return self._finalize_request(request_context, start_time, result)

        if decision.intent == "utility.summary":
            result = await self._handle_summary(user_id, trimmed)
            return self._finalize_request(request_context, start_time, result)

        if decision.intent == "command.ask":
            _, payload = _split_command(trimmed)
            execution, _ = await self._request_llm(
                user_id,
                payload,
                mode="ask",
                dialog_context=dialog_context if isinstance(dialog_context, str) else None,
                dialog_message_count=dialog_message_count if isinstance(dialog_message_count, int) else None,
                memory_context=memory_context if isinstance(memory_context, str) else None,
                request_id=request_id if isinstance(request_id, str) else None,
                request_context=request_context,
            )
            result = self._build_llm_result(
                execution,
                intent=decision.intent,
                facts_only=self.is_facts_only(user_id),
                request_context=request_context,
            )
            return self._finalize_request(request_context, start_time, result)

        if decision.intent == "command.search":
            _, payload = _split_command(trimmed)
            result = await self.run_fact_answer(
                user_id,
                payload,
                facts_only=True,
                intent="command.search",
                request_context=request_context,
            )
            return self._finalize_request(request_context, start_time, result)

        execution, _ = await self._request_llm(
            user_id,
            trimmed,
            mode="ask",
            dialog_context=dialog_context if isinstance(dialog_context, str) else None,
            dialog_message_count=dialog_message_count if isinstance(dialog_message_count, int) else None,
            memory_context=memory_context if isinstance(memory_context, str) else None,
            request_id=request_id if isinstance(request_id, str) else None,
            request_context=request_context,
        )
        result = self._build_llm_result(
            execution,
            intent=decision.intent,
            facts_only=self.is_facts_only(user_id),
            request_context=request_context,
        )
        return self._finalize_request(request_context, start_time, result)

    def _finalize_request(
        self,
        request_context: RequestContext | None,
        start_time: float,
        result: OrchestratorResult,
    ) -> OrchestratorResult:
        duration_ms = elapsed_ms(start_time)
        log_event(
            LOGGER,
            request_context,
            component="orchestrator",
            event="orchestrator.end",
            status=result.status,
            duration_ms=duration_ms,
            intent=result.intent,
            mode=result.mode,
        )
        add_trace(
            request_context,
            step="orchestrator.end",
            component="orchestrator",
            name=result.intent,
            status=result.status,
            duration_ms=duration_ms,
        )
        return result

    def execute_task(
        self,
        user_id: int,
        task_name: str,
        payload: str,
        *,
        request_context: RequestContext | None = None,
    ) -> OrchestratorResult:
        executed_at = datetime.now(timezone.utc)
        allowed, error_message = self._ensure_allowed(user_id)
        if not allowed:
            result = refused(
                error_message,
                intent=f"task.{task_name}",
                mode="tool",
                debug={"reason": "access_denied"},
            )
            self._record_task_result(user_id, task_name, payload, result, executed_at)
            return ensure_valid(result)
        start_time = time.monotonic()
        args_shape = build_args_shape({"payload": payload})
        log_event(
            LOGGER,
            request_context,
            component="tool",
            event="tool.call.start",
            status="ok",
            tool_name=task_name,
            intent=f"task.{task_name}",
            args_shape=args_shape,
        )
        add_trace(
            request_context,
            step="tool.call",
            component="tool",
            name=task_name,
            status="start",
            duration_ms=0.0,
        )
        try:
            task = self._get_task(task_name)
            result = task.handler(payload)
        except TaskError as exc:
            result = error(
                str(exc),
                intent=f"task.{task_name}",
                mode="tool",
                debug=_tool_debug_payload(request_context, exc, reason="task_error"),
            )
            LOGGER.warning("Task execution failed: %s", exc, exc_info=True)
            log_error(
                LOGGER,
                request_context,
                component="tool",
                where="tool.dispatch",
                exc=exc,
                extra={"tool_name": task_name},
            )
        except Exception as exc:  # pragma: no cover - safety net
            result = error(
                "Unexpected error while executing task.",
                intent=f"task.{task_name}",
                mode="tool",
                debug=_tool_debug_payload(request_context, exc, reason="unexpected_exception"),
            )
            LOGGER.exception("Unexpected error while executing task: %s", exc)
            log_error(
                LOGGER,
                request_context,
                component="tool",
                where="tool.dispatch",
                exc=exc,
                extra={"tool_name": task_name},
            )
        self._record_task_result(user_id, task_name, payload, result, executed_at)
        duration_ms = elapsed_ms(start_time)
        log_event(
            LOGGER,
            request_context,
            component="tool",
            event="tool.call.end",
            status=result.status,
            duration_ms=duration_ms,
            tool_name=task_name,
        )
        add_trace(
            request_context,
            step="orchestrator.dispatch",
            component="orchestrator",
            name=task_name,
            status=result.status,
            duration_ms=duration_ms,
        )
        add_trace(
            request_context,
            step="tool.call",
            component="tool",
            name=task_name,
            status=result.status,
            duration_ms=duration_ms,
        )
        return ensure_valid(result)

    async def ask_llm(
        self,
        user_id: int,
        prompt: str,
        *,
        mode: str = "ask",
        system_prompt: str | None = None,
        dialog_context: str | None = None,
        dialog_message_count: int | None = None,
        memory_context: str | None = None,
        request_id: str | None = None,
        request_context: RequestContext | None = None,
    ) -> TaskExecutionResult:
        execution, _ = await self._request_llm(
            user_id,
            prompt,
            mode=mode,
            system_prompt=system_prompt,
            dialog_context=dialog_context,
            dialog_message_count=dialog_message_count,
            memory_context=memory_context,
            request_id=request_id,
            request_context=request_context,
        )
        return execution

    def set_facts_only(self, user_id: int, enabled: bool) -> None:
        self._facts_only_by_user[user_id] = enabled

    def is_facts_only(self, user_id: int) -> bool:
        return self._facts_only_by_user.get(user_id, self._facts_only_default)

    async def _request_llm(
        self,
        user_id: int,
        prompt: str,
        *,
        mode: str = "ask",
        system_prompt: str | None = None,
        dialog_context: str | None = None,
        dialog_message_count: int | None = None,
        memory_context: str | None = None,
        request_id: str | None = None,
        request_context: RequestContext | None = None,
    ) -> tuple[TaskExecutionResult, list[str]]:
        executed_at = datetime.now(timezone.utc)
        trimmed = prompt.strip()
        sources_requested = is_sources_request(trimmed)
        allow_source_citations = mode == "search"
        if not trimmed:
            execution = self._error_execution(user_id, mode, prompt, "Запрос пустой.", executed_at)
            return execution, []
        if len(trimmed) > self._MAX_INPUT_LENGTH:
            execution = self._error_execution(
                user_id,
                mode,
                trimmed,
                "Слишком длинный запрос. Попробуйте короче.",
                executed_at,
            )
            return execution, []
        allowed, error_message = self._ensure_allowed(user_id)
        if not allowed:
            execution = self._error_execution(user_id, mode, trimmed, error_message, executed_at)
            return execution, []
        if self._rate_limiter is not None:
            allowed, rate_message = self._rate_limiter.check(user_id)
            if not allowed:
                execution = self._error_execution(user_id, mode, trimmed, rate_message, executed_at)
                return execution, []
        llm_client = self._llm_client
        if llm_client is None or not getattr(llm_client, "api_key", None):
            execution = self._error_execution(
                user_id,
                mode,
                trimmed,
                "LLM не настроен: OPENAI_API_KEY или PERPLEXITY_API_KEY",
                executed_at,
            )
            return execution, []
        else:
            llm_config = self._config.get("llm", {})
            model = self._llm_model or llm_config.get("model", "sonar")
            provider = _resolve_llm_provider(llm_client)
            llm_trace_name = f"{provider}/{model}" if provider else model
            effective_system_prompt = system_prompt if system_prompt is not None else llm_config.get("system_prompt")
            if mode == "search":
                effective_system_prompt = llm_config.get(
                    "search_system_prompt",
                    effective_system_prompt,
                )
            def _build_messages(request_prompt: str) -> list[dict[str, Any]]:
                messages: list[dict[str, Any]] = []
                if effective_system_prompt:
                    messages.append(
                        {
                            "role": "system",
                            "content": f"{effective_system_prompt}\n\n{_PLAIN_TEXT_SYSTEM_PROMPT}",
                        }
                    )
                else:
                    messages.append({"role": "system", "content": _PLAIN_TEXT_SYSTEM_PROMPT})
                history_turns = self._resolve_history_turns(llm_config)
                if history_turns > 0:
                    recent = self._storage.get_recent_executions(
                        user_id,
                        task_names=["ask", "search"],
                        limit=history_turns,
                    )
                    for record in recent:
                        if record["status"] != "success":
                            continue
                        messages.append({"role": "user", "content": record["payload"]})
                        messages.append({"role": "assistant", "content": record["result"]})
                combined_prompt = request_prompt
                memory_text = memory_context.strip() if isinstance(memory_context, str) else ""
                dialog_text = dialog_context.strip() if isinstance(dialog_context, str) else ""
                context_blocks = [block for block in [memory_text, dialog_text] if block]
                context_text = "\n\n".join(context_blocks)
                if context_text:
                    combined_prompt = f"{context_text}\n\n{request_prompt}"
                    count_messages = dialog_message_count if isinstance(dialog_message_count, int) else None
                    LOGGER.info(
                        "LLM context applied: user_id=%s count_messages=%s request_id=%s",
                        user_id,
                        count_messages if count_messages is not None else "unknown",
                        request_id or "-",
                    )
                    LOGGER.debug(
                        "LLM context details: user_id=%s count_messages=%s chars=%s",
                        user_id,
                        count_messages if count_messages is not None else "unknown",
                        len(context_text),
                    )
                messages.append({"role": "user", "content": combined_prompt})
                return messages

            breaker = self._circuit_breakers.get("llm")
            allowed, circuit_event = breaker.allow_request()
            if circuit_event:
                log_event(
                    LOGGER,
                    request_context,
                    component="llm",
                    event=circuit_event,
                    status="ok",
                    name=llm_trace_name,
                )
            if not allowed:
                log_event(
                    LOGGER,
                    request_context,
                    component="llm",
                    event="circuit.short_circuit",
                    status="error",
                    name=llm_trace_name,
                )
                add_trace(
                    request_context,
                    step="llm.call",
                    component="llm",
                    name=llm_trace_name,
                    status="error",
                    duration_ms=0.0,
                )
                execution = self._error_execution(
                    user_id,
                    mode,
                    trimmed,
                    map_error_text("temporarily_unavailable"),
                    executed_at,
                )
                return execution, []
            start_time = time.monotonic()
            log_event(
                LOGGER,
                request_context,
                component="llm",
                event="llm.call.start",
                status="ok",
                mode=mode,
                prompt=trimmed,
                model=model,
                provider=provider or "-",
                request_id=request_id or "-",
            )
            add_trace(
                request_context,
                step="llm.call",
                component="llm",
                name=llm_trace_name,
                status="start",
                duration_ms=0.0,
            )
            try:
                messages = _build_messages(trimmed)
                response_text = await retry_async(
                    lambda: llm_client.generate_text(
                        model=model,
                        messages=messages,
                        web_search_options=None,
                    ),
                    policy=self._retry_policy,
                    timeout_seconds=self._timeouts.llm_seconds,
                    logger=LOGGER,
                    request_context=request_context,
                    component="llm",
                    name=llm_trace_name,
                    is_retryable=self._is_retryable_exception,
                )
                result = ensure_plain_text(response_text)
                sanitized, meta = sanitize_llm_text(
                    result,
                    sources_requested=sources_requested,
                    allow_source_citations=allow_source_citations,
                )
                if sources_requested and meta.get("needs_regeneration"):
                    regen_instruction = (
                        "Объясни простыми словами, без чисел, без терминов, без науки, "
                        "как для человека без медицинских знаний."
                    )
                    regen_prompt = f"{trimmed}\n\n{regen_instruction}"
                    regen_messages = _build_messages(regen_prompt)
                    response_text = await retry_async(
                        lambda: llm_client.generate_text(
                            model=model,
                            messages=regen_messages,
                            web_search_options=None,
                        ),
                        policy=self._retry_policy,
                        timeout_seconds=self._timeouts.llm_seconds,
                        logger=LOGGER,
                        request_context=request_context,
                        component="llm",
                        name=llm_trace_name,
                        is_retryable=self._is_retryable_exception,
                    )
                    result = ensure_plain_text(response_text)
                    sanitized, meta = sanitize_llm_text(
                        result,
                        sources_requested=sources_requested,
                        allow_source_citations=allow_source_citations,
                    )
                if meta["failed"]:
                    result = SAFE_FALLBACK_TEXT
                    if sources_requested and not allow_source_citations:
                        result = f"{SOURCES_DISCLAIMER_TEXT}\n{result}"
                    LOGGER.warning(
                        "LLM text sanitization failed: user_id=%s mode=%s meta=%s",
                        user_id,
                        mode,
                        meta,
                    )
                else:
                    result = sanitized
                status = "success"
                circuit_event = breaker.record_success()
                if circuit_event:
                    log_event(
                        LOGGER,
                        request_context,
                        component="llm",
                        event=circuit_event,
                        status="ok",
                        name=llm_trace_name,
                    )
            except LLMGuardError as exc:
                result = "Некорректный ответ LLM. Попробуйте позже."
                status = "error"
                circuit_event = breaker.record_failure()
                if circuit_event:
                    log_event(
                        LOGGER,
                        request_context,
                        component="llm",
                        event=circuit_event,
                        status="error",
                        name=llm_trace_name,
                    )
                if request_context:
                    request_context.meta["llm_error"] = _llm_error_payload(request_context, exc)
                LOGGER.warning("LLM guard error: %s", exc)
                log_error(
                    LOGGER,
                    request_context,
                    component="llm",
                    where="llm.client",
                    exc=exc,
                    extra={"mode": mode, "model": model, "provider": provider or "-"},
                )
            except LLMAPIError as exc:
                result = self._map_llm_error(exc)
                status = "error"
                circuit_event = breaker.record_failure()
                if circuit_event:
                    log_event(
                        LOGGER,
                        request_context,
                        component="llm",
                        event=circuit_event,
                        status="error",
                        name=llm_trace_name,
                    )
                if request_context:
                    request_context.meta["llm_error"] = _llm_error_payload(request_context, exc)
                LOGGER.warning(
                    "LLM API error: status=%s user_id=%s",
                    exc.status_code,
                    user_id,
                )
                log_error(
                    LOGGER,
                    request_context,
                    component="llm",
                    where="llm.client",
                    exc=exc,
                    extra={"mode": mode, "model": model, "provider": provider or "-"},
                )
            except asyncio.TimeoutError as exc:
                result = map_error_text("timeout")
                status = "error"
                circuit_event = breaker.record_failure()
                if circuit_event:
                    log_event(
                        LOGGER,
                        request_context,
                        component="llm",
                        event=circuit_event,
                        status="error",
                        name=llm_trace_name,
                    )
                if request_context:
                    request_context.meta["llm_error"] = _llm_error_payload(request_context, exc)
                log_error(
                    LOGGER,
                    request_context,
                    component="llm",
                    where="llm.timeout",
                    exc=exc,
                    extra={"mode": mode, "model": model, "provider": provider or "-"},
                )
            except Exception as exc:
                result = map_error_text("temporarily_unavailable")
                status = "error"
                circuit_event = breaker.record_failure()
                if circuit_event:
                    log_event(
                        LOGGER,
                        request_context,
                        component="llm",
                        event=circuit_event,
                        status="error",
                        name=llm_trace_name,
                    )
                if request_context:
                    request_context.meta["llm_error"] = _llm_error_payload(request_context, exc)
                LOGGER.warning("LLM request failed: %s", exc, exc_info=True)
                log_error(
                    LOGGER,
                    request_context,
                    component="llm",
                    where="llm.client",
                    exc=exc,
                    extra={"mode": mode, "model": model, "provider": provider or "-"},
                )
            finally:
                duration_ms = elapsed_ms(start_time)
                log_event(
                    LOGGER,
                    request_context,
                    component="llm",
                    event="llm.call.end",
                    status=status,
                    duration_ms=duration_ms,
                    mode=mode,
                    model=model,
                    provider=provider or "-",
                )
                add_trace(
                    request_context,
                    step="llm.call",
                    component="llm",
                    name=llm_trace_name,
                    status=status,
                    duration_ms=duration_ms,
                )

        execution = TaskExecutionResult(
            task_name=mode,
            payload=prompt,
            result=result,
            status=status,
            executed_at=executed_at,
            user_id=user_id,
        )
        self._storage.record_execution(execution)
        return execution, []

    async def search_llm(self, user_id: int, prompt: str) -> TaskExecutionResult:
        return await self.ask_llm(user_id, prompt, mode="search")

    async def handle_text(self, user_id: int, text: str) -> OrchestratorResult:
        allowed, error_message = self._ensure_allowed(user_id)
        if not allowed:
            return ensure_valid(
                refused(
                    error_message,
                    intent="text.access_denied",
                    mode="local",
                    debug={"reason": "access_denied"},
                )
            )

        trimmed = text.strip()
        if not trimmed:
            return ensure_valid(
                refused(
                    "Запрос пустой.",
                    intent="text.empty",
                    mode="local",
                    debug={"reason": "empty_prompt"},
                )
            )
        if len(trimmed) > self._MAX_INPUT_LENGTH:
            return ensure_valid(
                refused(
                    "Слишком длинный запрос. Попробуйте короче.",
                    intent="text.too_long",
                    mode="local",
                    debug={"reason": "input_too_long"},
                )
            )
        LOGGER.info("Incoming message: user_id=%s text_preview=%s", user_id, trimmed[:200])
        lower = trimmed.lower()
        # Explicit task shortcut: !<task> <payload>
        # Example: !echo hello
        if trimmed.startswith("!"):
            payload = trimmed[1:].strip()
            if not payload:
                return ensure_valid(
                    error(
                        "Формат: task <name> <payload>",
                        intent="task.run",
                        mode="tool",
                        debug={"reason": "missing_payload"},
                    )
                )
            parts = payload.split(maxsplit=1)
            if len(parts) < 2:
                return ensure_valid(
                    error(
                        "Формат: task <name> <payload>",
                        intent="task.run",
                        mode="tool",
                        debug={"reason": "missing_payload"},
                    )
                )
            task_name, task_payload = parts[0], parts[1].strip()
            if not task_payload:
                return ensure_valid(
                    error(
                        "Формат: task <name> <payload>",
                        intent="task.run",
                        mode="tool",
                        debug={"reason": "missing_payload"},
                    )
                )
            LOGGER.info("Routing: user_id=%s action=task name=%s (!)", user_id, task_name)
            return self.execute_task(user_id, task_name, task_payload)
        if lower.startswith("task ") or lower.startswith("task:"):
            payload = trimmed[5:] if lower.startswith("task ") else trimmed[5:]
            payload = payload.strip()
            if not payload:
                return ensure_valid(
                    error(
                        "Формат: task <name> <payload>",
                        intent="task.run",
                        mode="tool",
                        debug={"reason": "missing_payload"},
                    )
                )
            parts = payload.split(maxsplit=1)
            if len(parts) < 2:
                return ensure_valid(
                    error(
                        "Формат: task <name> <payload>",
                        intent="task.run",
                        mode="tool",
                        debug={"reason": "missing_payload"},
                    )
                )
            task_name, task_payload = parts[0], parts[1].strip()
            if not task_payload:
                return ensure_valid(
                    error(
                        "Формат: task <name> <payload>",
                        intent="task.run",
                        mode="tool",
                        debug={"reason": "missing_payload"},
                    )
                )
            LOGGER.info("Routing: user_id=%s action=task name=%s", user_id, task_name)
            return self.execute_task(user_id, task_name, task_payload)

        if lower.startswith("echo ") or lower.startswith("echo:"):
            payload = trimmed[4:].strip()
            if not payload:
                return ensure_valid(
                    error(
                        "Формат: task <name> <payload>",
                        intent="task.run",
                        mode="tool",
                        debug={"reason": "missing_payload"},
                    )
                )
            LOGGER.info("Routing: user_id=%s action=local name=echo", user_id)
            return self.execute_task(user_id, "echo", payload)

        if lower.startswith("upper ") or lower.startswith("upper:"):
            payload = trimmed[5:].strip()
            if not payload:
                return ensure_valid(
                    error(
                        "Формат: task <name> <payload>",
                        intent="task.run",
                        mode="tool",
                        debug={"reason": "missing_payload"},
                    )
                )
            LOGGER.info("Routing: user_id=%s action=local name=upper", user_id)
            return self.execute_task(user_id, "upper", payload)

        if lower.startswith("json_pretty ") or lower.startswith("json_pretty:"):
            payload = trimmed[12:].strip()
            if not payload:
                return ensure_valid(
                    error(
                        "Формат: task <name> <payload>",
                        intent="task.run",
                        mode="tool",
                        debug={"reason": "missing_payload"},
                    )
                )
            LOGGER.info("Routing: user_id=%s action=local name=json_pretty", user_id)
            return self.execute_task(user_id, "json_pretty", payload)

        if lower.startswith("/search"):
            payload = trimmed[7:].strip()
            if not payload:
                return ensure_valid(
                    refused(
                        "Укажи запрос: /search <текст>.",
                        intent="command.search",
                        mode="local",
                        debug={"reason": "missing_payload"},
                    )
                )
            return await self.run_fact_answer(user_id, payload, facts_only=True, intent="command.search")

        if lower.startswith("search ") or lower.startswith("search:"):
            payload = trimmed[7:].strip()
            if not payload:
                return ensure_valid(
                    refused(
                        "Укажи запрос: /search <текст>.",
                        intent="command.search",
                        mode="local",
                        debug={"reason": "missing_payload"},
                    )
                )
            return await self.run_fact_answer(user_id, payload, facts_only=True, intent="command.search")

        LOGGER.info("Routing: user_id=%s action=perplexity mode=ask", user_id)
        execution = await self.ask_llm(user_id, trimmed, mode="ask")
        return self._build_llm_result(execution, intent="ask.llm", facts_only=False, request_context=None)

    async def run_fact_answer(
        self,
        user_id: int,
        query: str,
        *,
        facts_only: bool,
        intent: str,
        request_context: RequestContext | None = None,
    ) -> OrchestratorResult:
        if not self._feature_web_search:
            return ensure_valid(
                refused(
                    "Поиск временно отключён.",
                    intent=intent,
                    mode="tool",
                    debug={"reason": "web_search_disabled"},
                )
            )
        trimmed_query = query.strip()
        if not trimmed_query:
            return ensure_valid(
                refused(
                    "Укажи запрос: /search <текст>.",
                    intent=intent,
                    mode="local",
                    debug={"reason": "missing_payload"},
                )
            )
        started_at = time.monotonic()
        breaker = self._circuit_breakers.get("web_search")
        allowed, circuit_event = breaker.allow_request()
        if circuit_event:
            log_event(
                LOGGER,
                request_context,
                component="web",
                event=circuit_event,
                status="ok",
                name="web_search",
            )
        if not allowed:
            log_event(
                LOGGER,
                request_context,
                component="web",
                event="circuit.short_circuit",
                status="error",
                name="web_search",
            )
            add_trace(
                request_context,
                step="web.search",
                component="web",
                name="web_search",
                status="error",
                duration_ms=elapsed_ms(started_at),
            )
            return ensure_valid(
                error(
                    map_error_text("temporarily_unavailable"),
                    intent=intent,
                    mode="tool",
                    debug={"reason": "circuit_open"},
                )
            )
        log_event(
            LOGGER,
            request_context,
            component="web",
            event="web.search.start",
            status="ok",
            query=trimmed_query,
        )
        add_trace(
            request_context,
            step="web.search",
            component="web",
            name="web_search",
            status="start",
            duration_ms=0.0,
        )
        status = "success"
        search_failed = False
        try:
            sources = await retry_async(
                lambda: self._search_client.search(trimmed_query, max_results=5),
                policy=self._retry_policy,
                timeout_seconds=self._timeouts.web_tool_call_seconds,
                logger=LOGGER,
                request_context=request_context,
                component="web",
                name="web_search",
                is_retryable=self._is_retryable_exception,
            )
            circuit_event = breaker.record_success()
            if circuit_event:
                log_event(
                    LOGGER,
                    request_context,
                    component="web",
                    event=circuit_event,
                    status="ok",
                    name="web_search",
                )
        except asyncio.TimeoutError as exc:
            status = "error"
            circuit_event = breaker.record_failure()
            if circuit_event:
                log_event(
                    LOGGER,
                    request_context,
                    component="web",
                    event=circuit_event,
                    status="error",
                    name="web_search",
                )
            log_error(
                LOGGER,
                request_context,
                component="web",
                where="web.search.timeout",
                exc=exc,
            )
            sources = []
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
            search_failed = True
            circuit_event = breaker.record_failure()
            if circuit_event:
                log_event(
                    LOGGER,
                    request_context,
                    component="web",
                    event=circuit_event,
                    status="error",
                    name="web_search",
                )
            log_error(
                LOGGER,
                request_context,
                component="web",
                where="web.search",
                exc=exc,
            )
            LOGGER.warning("Web search failed: %s", exc, exc_info=True)
            sources = []
        finally:
            duration_ms = elapsed_ms(started_at)
            log_event(
                LOGGER,
                request_context,
                component="web",
                event="web.search.end",
                status=status,
                duration_ms=duration_ms,
            )
            add_trace(
                request_context,
                step="web.search",
                component="web",
                name="web_search",
                status=status,
                duration_ms=duration_ms,
            )

        if not sources and search_failed:
            return ensure_valid(
                error(
                    map_error_text("temporarily_unavailable"),
                    intent=intent,
                    mode="tool",
                    debug={"reason": "web_search_failed"},
                )
            )
        if not sources:
            reason = "facts_only_no_sources" if facts_only else "search_no_results"
            return ensure_valid(
                refused(
                    "Не могу ответить без источников. Попробуй переформулировать запрос.",
                    intent=intent,
                    mode="tool",
                    debug={"reason": reason, "query": trimmed_query},
                )
            )

        sources_prompt = build_sources_prompt(sources)
        llm_prompt = (
            f"Вопрос пользователя: {trimmed_query}\n\n"
            f"{sources_prompt}\n\n"
            "Инструкция: Отвечай строго по источникам. Каждый факт помечай ссылками [N]. "
            "Не придумывай. Если в источниках нет ответа — скажи, что данных нет."
        )
        execution, _ = await self._request_llm(user_id, llm_prompt, mode="search")
        if execution.status != "success":
            return ensure_valid(
                error(
                    execution.result,
                    intent=intent,
                    mode="llm",
                    debug={"task_name": execution.task_name, "reason": "search_llm_error"},
                )
            )

        rendered = render_fact_response_with_sources(execution.result, sources)
        result = ok(
            rendered,
            intent=intent,
            mode="llm",
            sources=sources,
            debug={
                "provider": "perplexity_search",
                "sources_count": len(sources),
                "latency_seconds": round(time.monotonic() - started_at, 3),
                "query": trimmed_query,
            },
        )
        return ensure_valid(ensure_safe_text_strict(result, facts_enabled=facts_only, allow_sources_in_text=True))

    def is_allowed(self, user_id: int) -> bool:
        return self._ensure_allowed(user_id)[0]

    def is_access_restricted(self) -> bool:
        return bool(self._access and self._access.is_restricted())

    def _get_task(self, task_name: str) -> TaskDefinition:
        registry = self._registry
        if task_name not in registry:
            raise TaskNotFoundError(f"Unknown task: {task_name}.")
        if task_name not in self._enabled_tasks():
            raise TaskDisabledError(f"Task '{task_name}' is disabled.")
        return registry[task_name]

    def _enabled_tasks(self) -> set[str]:
        tasks_config = self._config.get("tasks", {})
        enabled = tasks_config.get("enabled")
        disabled = set(tasks_config.get("disabled", []))
        if isinstance(enabled, list):
            return set(enabled)
        return set(self._registry.keys()) - disabled

    def _ensure_allowed(self, user_id: int) -> tuple[bool, str]:
        if self._access is None:
            return True, ""
        if self._access.is_allowed(user_id):
            return True, ""
        return False, "Доступ запрещён."

    def _resolve_history_turns(self, llm_config: dict[str, Any]) -> int:
        if self._llm_history_turns is not None:
            return self._llm_history_turns
        history_turns = llm_config.get("history_turns")
        if isinstance(history_turns, int):
            return history_turns
        return 0

    def _error_execution(
        self,
        user_id: int,
        task_name: str,
        payload: str,
        result: str,
        executed_at: datetime | None = None,
    ) -> TaskExecutionResult:
        execution = TaskExecutionResult(
            task_name=task_name,
            payload=payload,
            result=result,
            status="error",
            executed_at=executed_at or datetime.now(timezone.utc),
            user_id=user_id,
        )
        self._storage.record_execution(execution)
        return execution

    def _map_llm_error(self, exc: LLMAPIError) -> str:
        if exc.status_code in {401, 403}:
            return map_error_text("auth_required")
        if exc.status_code == 429:
            return map_error_text("rate_limited")
        if exc.status_code >= 500:
            return map_error_text("temporarily_unavailable")
        return map_error_text("temporarily_unavailable")

    def _is_retryable_exception(self, exc: Exception) -> bool:
        if is_timeout_error(exc) or is_network_error(exc):
            return True
        if isinstance(exc, LLMAPIError):
            return exc.status_code == 429 or exc.status_code >= 500
        return False

    async def _handle_summary(self, user_id: int, text: str) -> OrchestratorResult:
        payload = _extract_summary_payload(text)
        if not payload:
            return ensure_valid(
                refused(
                    "Использование: summary: <текст> или /summary <текст>.",
                    intent="utility.summary",
                    mode="local",
                    debug={"reason": "missing_summary_payload"},
                )
            )
        system_prompt = "Кратко, 5-8 пунктов, без выдумок и домыслов."
        execution, _ = await self._request_llm(
            user_id,
            payload,
            mode="summary",
            system_prompt=system_prompt,
        )
        if execution.status != "success":
            status = "error"
            if "LLM не настроен" in execution.result:
                status = "refused"
            result = (
                error(
                    execution.result,
                    intent="utility.summary",
                    mode="llm",
                    debug={"task_name": execution.task_name},
                )
                if status == "error"
                else refused(
                    execution.result,
                    intent="utility.summary",
                    mode="llm",
                    debug={"task_name": execution.task_name},
                )
            )
            return ensure_valid(result)
        return self._build_llm_result(
            execution,
            intent="utility.summary",
            facts_only=self.is_facts_only(user_id),
            request_context=None,
        )

    def _build_llm_result(
        self,
        execution: TaskExecutionResult,
        *,
        intent: str,
        facts_only: bool,
        request_context: RequestContext | None,
    ) -> OrchestratorResult:
        status = "ok" if execution.status == "success" else "error"
        sources: list[Source] = []
        if facts_only:
            return ensure_valid(
                refused(
                    "Не могу подтвердить источниками. Переформулируй или попроси без режима фактов.",
                    intent=intent,
                    mode="llm",
                    debug={"reason": "facts_only_no_sources"},
                )
            )
        final_text = execution.result
        debug_payload: dict[str, Any] = {"task_name": execution.task_name}
        if status == "error" and request_context:
            llm_error = request_context.meta.get("llm_error")
            if llm_error:
                debug_payload["llm_error"] = llm_error
        result = (
            ok(
                final_text,
                intent=intent,
                mode="llm",
                sources=sources,
                debug=debug_payload,
            )
            if status == "ok"
            else error(
                final_text,
                intent=intent,
                mode="llm",
                sources=sources,
                debug=debug_payload,
            )
        )
        return ensure_valid(result)

    def _make_decision(self, trimmed: str) -> Decision:
        if not trimmed:
            return Decision(intent="intent.unknown", status="refused", reason="empty_prompt")
        if len(trimmed) > self._MAX_INPUT_LENGTH:
            return Decision(intent="intent.unknown", status="refused", reason="input_too_long")
        lowered = trimmed.lower()
        if _is_destructive_request(lowered):
            return Decision(intent="refused.destructive", status="refused", reason="destructive")
        if detect_intent(trimmed) == "utility.summary" and not _extract_summary_payload(trimmed):
            return Decision(intent="utility.summary", status="refused", reason="missing_summary_payload")
        if trimmed.startswith("/"):
            command, payload = _split_command(trimmed)
            if command == "/ask":
                if not payload:
                    return Decision(intent="command.ask", status="refused", reason="missing_payload")
                return Decision(intent="command.ask", status="ok")
            if command == "/summary":
                if not payload:
                    return Decision(intent="utility.summary", status="refused", reason="missing_summary_payload")
                return Decision(intent="utility.summary", status="ok")
            if command == "/search":
                if not payload:
                    return Decision(intent="command.search", status="refused", reason="missing_search_payload")
                return Decision(intent="command.search", status="ok")
            return Decision(intent="command.unknown", status="refused", reason="unknown_command")
        intent = detect_intent(trimmed)
        return Decision(intent=intent, status="ok")

    def _result_from_decision(self, decision: Decision) -> OrchestratorResult:
        if decision.reason == "empty_prompt":
            return refused("Запрос пустой.", intent=decision.intent, mode="local")
        if decision.reason == "input_too_long":
            return refused(
                "Слишком длинный запрос. Попробуйте короче.",
                intent=decision.intent,
                mode="local",
            )
        if decision.reason == "missing_payload":
            return refused(
                "Введите текст запроса. Пример: /ask Привет",
                intent=decision.intent,
                mode="local",
            )
        if decision.reason == "missing_search_payload":
            return refused(
                "Укажи запрос: /search <текст>.",
                intent=decision.intent,
                mode="local",
            )
        if decision.reason == "missing_summary_payload":
            return refused(
                "Использование: summary: <текст> или /summary <текст>.",
                intent=decision.intent,
                mode="local",
            )
        if decision.reason == "destructive":
            return refused(_DESTRUCTIVE_REFUSAL, intent=decision.intent, mode="local")
        if decision.reason in {"unknown_command"}:
            return refused(_UNKNOWN_COMMAND_MESSAGE, intent=decision.intent, mode="local")
        return refused("Команда не поддерживается в этом режиме.", intent=decision.intent, mode="local")

    def _record_task_result(
        self,
        user_id: int,
        task_name: str,
        payload: str,
        result: OrchestratorResult,
        executed_at: datetime,
    ) -> None:
        status = "success" if result.status == "ok" else "error"
        execution = TaskExecutionResult(
            task_name=task_name,
            payload=payload,
            result=result.text,
            status=status,
            executed_at=executed_at,
            user_id=user_id,
        )
        self._storage.record_execution(execution)

    def _smalltalk_response(self, text: str) -> str:
        lowered = text.lower()
        if "привет" in lowered or "hello" in lowered or "hi" in lowered or "hey" in lowered:
            return "Привет! Чем помочь?"
        if "как дела" in lowered:
            return "Всё хорошо! Чем помочь?"
        if "спасибо" in lowered or "thanks" in lowered or "thank you" in lowered:
            return "Пожалуйста! Обращайтесь."
        if "пока" in lowered or "bye" in lowered or "goodbye" in lowered:
            return "Пока! Буду на связи."
        return "Привет! Чем помочь?"


def _extract_summary_payload(text: str) -> str:
    trimmed = text.strip()
    lowered = trimmed.lower()
    if lowered.startswith("summary:"):
        return trimmed[len("summary:") :].strip()
    if lowered.startswith("/summary"):
        parts = trimmed.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""
    return ""


def _split_command(text: str) -> tuple[str, str]:
    parts = text.split(maxsplit=1)
    command = parts[0].lower()
    payload = parts[1].strip() if len(parts) > 1 else ""
    return command, payload


def _is_destructive_request(lowered: str) -> bool:
    question_markers = ("как", "что", "где", "можно ли", "?")
    if any(marker in lowered for marker in question_markers):
        return False
    destructive_markers = (
        "удали все напоминания",
        "удалить все напоминания",
        "delete all reminders",
    )
    return any(marker in lowered for marker in destructive_markers)


def _extract_sources_from_text(text: str) -> list[Source]:
    urls = re.findall(r"https?://[^\s<>]+", text)
    cleaned: list[str] = []
    for url in urls:
        cleaned.append(url.rstrip(").,;\"'"))
    seen: set[str] = set()
    unique: list[Source] = []
    for url in cleaned:
        if url and url not in seen:
            seen.add(url)
            unique.append(Source(title=url, url=url, snippet=""))
    return unique


def _ensure_sources_in_text(text: str, sources: list[Source]) -> str:
    if not sources:
        return text
    if any(source.url in text for source in sources):
        return text
    lines = ["Источники:"]
    for index, source in enumerate(sources, start=1):
        lines.append(f"{index}) {source.url}")
    return f"{text}\n\n" + "\n".join(lines) if text else "\n".join(lines)


def _build_sources_from_citations(citations: list[str]) -> list[Source]:
    sources: list[Source] = []
    for url in citations:
        if isinstance(url, str) and url:
            sources.append(Source(title=url, url=url, snippet=""))
    return sources


def _resolve_llm_provider(client: LLMClient) -> str:
    provider = getattr(client, "provider", None)
    if isinstance(provider, str) and provider:
        return provider
    name = client.__class__.__name__
    if name.endswith("Client"):
        name = name[: -len("Client")]
    return name.lower()


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def load_orchestrator_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        LOGGER.warning("Orchestrator config not found at %s. Using defaults.", path)
        return {}
    with path.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)
