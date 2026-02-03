from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Literal

from app.core.models import TaskExecutionResult
from app.core.tasks import InvalidPayloadError, TaskDefinition, TaskError, get_task_registry
from app.infra.access import AccessController
from app.infra.llm import LLMAPIError, LLMClient
from app.infra.rate_limit import RateLimiter
from app.infra.storage import TaskStorage


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrchestratorResult:
    text: str
    status: Literal["ok", "refused", "error"]
    mode: Literal["local", "llm"]
    intent: str
    sources: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


def detect_intent(text: str) -> str:
    trimmed = text.strip()
    if not trimmed:
        return "unknown"
    lowered = trimmed.lower()
    if lowered.startswith("summary:") or lowered.startswith("/summary"):
        return "utility_summary"
    if trimmed.startswith("/"):
        return "command"
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
        return "smalltalk"
    return "question"


class TaskNotFoundError(TaskError):
    """Raised when task name is missing from registry."""


class TaskDisabledError(TaskError):
    """Raised when task is disabled by config."""


class Orchestrator:
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
    ) -> None:
        self._config = config
        self._storage = storage
        self._registry = registry or get_task_registry()
        self._llm_client = llm_client
        self._access = access
        self._rate_limiter = rate_limiter
        self._llm_history_turns = llm_history_turns
        self._llm_model = llm_model
        self._facts_only_default = _coerce_bool(config.get("facts_only_default", False))
        self._facts_only_by_user: dict[int, bool] = {}

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    def list_tasks(self) -> list[TaskDefinition]:
        enabled = self._enabled_tasks()
        return [self._registry[name] for name in enabled if name in self._registry]

    async def handle(self, text: str, user_context: dict[str, Any]) -> OrchestratorResult:
        user_id = int(user_context.get("user_id") or 0)
        dialog_context = user_context.get("dialog_context")
        dialog_message_count = user_context.get("dialog_message_count")
        request_id = user_context.get("request_id")
        allowed, error_message = self._ensure_allowed(user_id)
        if not allowed:
            return OrchestratorResult(
                text=error_message,
                status="refused",
                mode="local",
                intent="command",
                sources=[],
                debug={"reason": "access_denied"},
            )
        trimmed = text.strip()
        intent = detect_intent(text)
        if not trimmed:
            return OrchestratorResult(
                text="Запрос пустой.",
                status="refused",
                mode="local",
                intent=intent,
                sources=[],
                debug={"reason": "empty_prompt"},
            )

        if intent == "smalltalk":
            response = self._smalltalk_response(trimmed)
            return OrchestratorResult(
                text=response,
                status="ok",
                mode="local",
                intent=intent,
                sources=[],
                debug={"strategy": "smalltalk_local"},
            )

        if intent == "utility_summary":
            return await self._handle_summary(user_id, trimmed)

        if intent == "command":
            command, payload = _split_command(trimmed)
            if command in {"/ask", "/search"}:
                if not payload:
                    return OrchestratorResult(
                        text="Введите текст запроса. Пример: /ask Привет",
                        status="refused",
                        mode="local",
                        intent=intent,
                        sources=[],
                        debug={"reason": "missing_payload", "command": command},
                    )
                mode = "search" if command == "/search" else "ask"
                execution, citations = await self._request_llm(
                    user_id,
                    payload,
                    mode=mode,
                    dialog_context=dialog_context if isinstance(dialog_context, str) else None,
                    dialog_message_count=dialog_message_count if isinstance(dialog_message_count, int) else None,
                    request_id=request_id if isinstance(request_id, str) else None,
                )
                return self._build_llm_result(
                    execution,
                    citations,
                    intent=intent,
                    facts_only=self.is_facts_only(user_id),
                )
            if command == "/summary":
                return await self._handle_summary(user_id, trimmed)
            return OrchestratorResult(
                text="Команда не поддерживается в этом режиме.",
                status="refused",
                mode="local",
                intent=intent,
                sources=[],
                debug={"reason": "unsupported_command", "command": command},
            )

        execution, citations = await self._request_llm(
            user_id,
            trimmed,
            mode="ask",
            dialog_context=dialog_context if isinstance(dialog_context, str) else None,
            dialog_message_count=dialog_message_count if isinstance(dialog_message_count, int) else None,
            request_id=request_id if isinstance(request_id, str) else None,
        )
        return self._build_llm_result(
            execution,
            citations,
            intent=intent,
            facts_only=self.is_facts_only(user_id),
        )

    def execute_task(self, user_id: int, task_name: str, payload: str) -> TaskExecutionResult:
        executed_at = datetime.now(timezone.utc)
        allowed, error_message = self._ensure_allowed(user_id)
        if not allowed:
            execution = TaskExecutionResult(
                task_name=task_name,
                payload=payload,
                result=error_message,
                status="error",
                executed_at=executed_at,
                user_id=user_id,
            )
            self._storage.record_execution(execution)
            return execution
        try:
            task = self._get_task(task_name)
            result = task.handler(payload)
            status = "success"
        except TaskError as exc:
            result = str(exc)
            status = "error"
            LOGGER.warning("Task execution failed: %s", exc, exc_info=True)
        except Exception as exc:  # pragma: no cover - safety net
            result = "Unexpected error while executing task."
            status = "error"
            LOGGER.exception("Unexpected error while executing task: %s", exc)
        execution = TaskExecutionResult(
            task_name=task_name,
            payload=payload,
            result=result,
            status=status,
            executed_at=executed_at,
            user_id=user_id,
        )
        self._storage.record_execution(execution)
        return execution

    async def ask_llm(
        self,
        user_id: int,
        prompt: str,
        *,
        mode: str = "ask",
        system_prompt: str | None = None,
        dialog_context: str | None = None,
        dialog_message_count: int | None = None,
        request_id: str | None = None,
    ) -> TaskExecutionResult:
        execution, _ = await self._request_llm(
            user_id,
            prompt,
            mode=mode,
            system_prompt=system_prompt,
            dialog_context=dialog_context,
            dialog_message_count=dialog_message_count,
            request_id=request_id,
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
        request_id: str | None = None,
    ) -> tuple[TaskExecutionResult, list[str]]:
        executed_at = datetime.now(timezone.utc)
        trimmed = prompt.strip()
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
            effective_system_prompt = system_prompt if system_prompt is not None else llm_config.get("system_prompt")
            if mode == "search":
                effective_system_prompt = llm_config.get(
                    "search_system_prompt",
                    effective_system_prompt,
                )
            messages: list[dict[str, Any]] = []
            if effective_system_prompt:
                messages.append({"role": "system", "content": effective_system_prompt})
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
            combined_prompt = trimmed
            context_text = dialog_context.strip() if isinstance(dialog_context, str) else ""
            if context_text:
                combined_prompt = f"{context_text}\n\n{trimmed}"
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
            LOGGER.info(
                "LLM request: user_id=%s mode=%s prompt_len=%s history=%s",
                user_id,
                mode,
                len(trimmed),
                max(len(messages) - 1, 0),
            )
            start_time = time.monotonic()
            citations: list[str] = []
            try:
                web_search_options = None
                if mode == "search":
                    web_search_options = {"search_context_size": "medium"}
                response = await llm_client.create_chat_completion(
                    model=model,
                    messages=messages,
                    web_search_options=web_search_options,
                )
                result = response.get("content", "")
                citations = response.get("citations") or []
                if mode == "search":
                    if citations:
                        lines = ["Источники:"]
                        for index, url in enumerate(citations, start=1):
                            lines.append(f"{index}) {url}")
                        result = f"{result}\n\n" + "\n".join(lines) if result else "\n".join(lines)
                status = "success"
            except LLMAPIError as exc:
                result = self._map_llm_error(exc)
                status = "error"
                LOGGER.warning(
                    "LLM API error: status=%s user_id=%s",
                    exc.status_code,
                    user_id,
                )
            except Exception as exc:
                result = "Временная ошибка сервиса. Попробуйте позже."
                status = "error"
                LOGGER.warning("LLM request failed: %s", exc, exc_info=True)
            finally:
                duration = time.monotonic() - start_time
                LOGGER.info("LLM response: user_id=%s mode=%s duration=%.2fs", user_id, mode, duration)

        execution = TaskExecutionResult(
            task_name=mode,
            payload=prompt,
            result=result,
            status=status,
            executed_at=executed_at,
            user_id=user_id,
        )
        self._storage.record_execution(execution)
        return execution, citations

    async def search_llm(self, user_id: int, prompt: str) -> TaskExecutionResult:
        return await self.ask_llm(user_id, prompt, mode="search")

    async def handle_text(self, user_id: int, text: str) -> TaskExecutionResult:
        allowed, error_message = self._ensure_allowed(user_id)
        if not allowed:
            return self._error_execution(user_id, "text", text, error_message)

        trimmed = text.strip()
        if not trimmed:
            return self._error_execution(user_id, "text", text, "Запрос пустой.")
        if len(trimmed) > self._MAX_INPUT_LENGTH:
            return self._error_execution(
                user_id,
                "text",
                trimmed,
                "Слишком длинный запрос. Попробуйте короче.",
            )
        LOGGER.info("Incoming message: user_id=%s text_preview=%s", user_id, trimmed[:200])
        lower = trimmed.lower()
        # Explicit task shortcut: !<task> <payload>
        # Example: !echo hello
        if trimmed.startswith("!"):
            payload = trimmed[1:].strip()
            if not payload:
                return self._task_parse_error(user_id, text)
            parts = payload.split(maxsplit=1)
            if len(parts) < 2:
                return self._task_parse_error(user_id, text)
            task_name, task_payload = parts[0], parts[1].strip()
            if not task_payload:
                return self._task_parse_error(user_id, text)
            LOGGER.info("Routing: user_id=%s action=task name=%s (!)", user_id, task_name)
            return self.execute_task(user_id, task_name, task_payload)
        if lower.startswith("task ") or lower.startswith("task:"):
            payload = trimmed[5:] if lower.startswith("task ") else trimmed[5:]
            payload = payload.strip()
            if not payload:
                return self._task_parse_error(user_id, text)
            parts = payload.split(maxsplit=1)
            if len(parts) < 2:
                return self._task_parse_error(user_id, text)
            task_name, task_payload = parts[0], parts[1].strip()
            if not task_payload:
                return self._task_parse_error(user_id, text)
            LOGGER.info("Routing: user_id=%s action=task name=%s", user_id, task_name)
            return self.execute_task(user_id, task_name, task_payload)

        if lower.startswith("echo ") or lower.startswith("echo:"):
            payload = trimmed[4:].strip()
            if not payload:
                return self._task_parse_error(user_id, text)
            LOGGER.info("Routing: user_id=%s action=local name=echo", user_id)
            return self.execute_task(user_id, "echo", payload)

        if lower.startswith("upper ") or lower.startswith("upper:"):
            payload = trimmed[5:].strip()
            if not payload:
                return self._task_parse_error(user_id, text)
            LOGGER.info("Routing: user_id=%s action=local name=upper", user_id)
            return self.execute_task(user_id, "upper", payload)

        if lower.startswith("json_pretty ") or lower.startswith("json_pretty:"):
            payload = trimmed[12:].strip()
            if not payload:
                return self._task_parse_error(user_id, text)
            LOGGER.info("Routing: user_id=%s action=local name=json_pretty", user_id)
            return self.execute_task(user_id, "json_pretty", payload)

        if lower.startswith("/search "):
            payload = trimmed[8:].strip()
            if not payload:
                return self._error_execution(
                    user_id,
                    "search",
                    "",
                    "Введите текст поиска. Пример: /search Новости",
                )
            LOGGER.info("Routing: user_id=%s action=perplexity mode=search", user_id)
            return await self.search_llm(user_id, payload)

        if lower.startswith("search ") or lower.startswith("search:"):
            payload = trimmed[7:].strip()
            if not payload:
                return self._error_execution(
                    user_id,
                    "search",
                    "",
                    "Введите текст поиска. Пример: search Новости",
                )
            LOGGER.info("Routing: user_id=%s action=perplexity mode=search", user_id)
            return await self.search_llm(user_id, payload)

        LOGGER.info("Routing: user_id=%s action=perplexity mode=ask", user_id)
        return await self.ask_llm(user_id, trimmed, mode="ask")

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
            return "Ключ не настроен или недействителен."
        if exc.status_code == 429:
            return "Лимит запросов, попробуйте позже."
        if exc.status_code >= 500:
            return "Временная ошибка сервиса. Попробуйте позже."
        return "Не удалось получить ответ от сервиса."

    def _task_parse_error(self, user_id: int, text: str) -> TaskExecutionResult:
        execution = TaskExecutionResult(
            task_name="task",
            payload=text,
            result="Формат: task <name> <payload>",
            status="error",
            executed_at=datetime.now(timezone.utc),
            user_id=user_id,
        )
        self._storage.record_execution(execution)
        return execution

    async def _handle_summary(self, user_id: int, text: str) -> OrchestratorResult:
        payload = _extract_summary_payload(text)
        if not payload:
            return OrchestratorResult(
                text="Использование: summary: <текст> или /summary <текст>.",
                status="refused",
                mode="local",
                intent="utility_summary",
                sources=[],
                debug={"reason": "missing_summary_payload"},
            )
        system_prompt = "Кратко, 5-8 пунктов, без выдумок и домыслов."
        execution, citations = await self._request_llm(
            user_id,
            payload,
            mode="summary",
            system_prompt=system_prompt,
        )
        if execution.status != "success":
            status = "error"
            if "LLM не настроен" in execution.result:
                status = "refused"
            return OrchestratorResult(
                text=execution.result,
                status=status,
                mode="llm",
                intent="utility_summary",
                sources=[],
                debug={"task_name": execution.task_name},
            )
        return self._build_llm_result(
            execution,
            citations,
            intent="utility_summary",
            facts_only=self.is_facts_only(user_id),
        )

    def _build_llm_result(
        self,
        execution: TaskExecutionResult,
        citations: list[str],
        *,
        intent: str,
        facts_only: bool,
    ) -> OrchestratorResult:
        status = "ok" if execution.status == "success" else "error"
        sources = citations if citations else _extract_sources_from_text(execution.result)
        if facts_only and not sources:
            return OrchestratorResult(
                text=(
                    "Не могу подтвердить источниками. "
                    "Переформулируй или попроси без режима фактов."
                ),
                status="refused",
                mode="llm",
                intent=intent,
                sources=[],
                debug={"reason": "facts_only_no_sources"},
            )
        final_text = _ensure_sources_in_text(execution.result, sources)
        return OrchestratorResult(
            text=final_text,
            status=status,
            mode="llm",
            intent=intent,
            sources=sources,
            debug={"task_name": execution.task_name},
        )

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


def _extract_sources_from_text(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s<>]+", text)
    cleaned = []
    for url in urls:
        cleaned.append(url.rstrip(").,;\"'"))
    seen: set[str] = set()
    unique = []
    for url in cleaned:
        if url and url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def _ensure_sources_in_text(text: str, sources: list[str]) -> str:
    if not sources:
        return text
    if any(source in text for source in sources):
        return text
    lines = ["Источники:"]
    for index, url in enumerate(sources, start=1):
        lines.append(f"{index}) {url}")
    return f"{text}\n\n" + "\n".join(lines) if text else "\n".join(lines)


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
