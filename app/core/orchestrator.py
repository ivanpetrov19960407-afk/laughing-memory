from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.models import TaskExecutionResult
from app.core.tasks import InvalidPayloadError, TaskDefinition, TaskError, get_task_registry
from app.infra.access import AccessController
from app.infra.llm import PerplexityClient
from app.infra.rate_limit import RateLimiter
from app.infra.storage import TaskStorage


LOGGER = logging.getLogger(__name__)


class TaskNotFoundError(TaskError):
    """Raised when task name is missing from registry."""


class TaskDisabledError(TaskError):
    """Raised when task is disabled by config."""


class Orchestrator:
    def __init__(
        self,
        config: dict[str, Any],
        storage: TaskStorage,
        registry: dict[str, TaskDefinition] | None = None,
        llm_client: PerplexityClient | None = None,
        access: AccessController | None = None,
        rate_limiter: RateLimiter | None = None,
        llm_history_turns: int | None = None,
    ) -> None:
        self._config = config
        self._storage = storage
        self._registry = registry or get_task_registry()
        self._llm_client = llm_client
        self._access = access
        self._rate_limiter = rate_limiter
        self._llm_history_turns = llm_history_turns

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    def list_tasks(self) -> list[TaskDefinition]:
        enabled = self._enabled_tasks()
        return [self._registry[name] for name in enabled if name in self._registry]

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

    async def ask_llm(self, user_id: int, prompt: str, *, mode: str = "ask") -> TaskExecutionResult:
        executed_at = datetime.now(timezone.utc)
        allowed, error_message = self._ensure_allowed(user_id)
        if not allowed:
            execution = TaskExecutionResult(
                task_name=mode,
                payload=prompt,
                result=error_message,
                status="error",
                executed_at=executed_at,
                user_id=user_id,
            )
            self._storage.record_execution(execution)
            return execution
        if self._rate_limiter is not None:
            allowed, rate_message = self._rate_limiter.check(user_id)
            if not allowed:
                execution = TaskExecutionResult(
                    task_name=mode,
                    payload=prompt,
                    result=rate_message,
                    status="error",
                    executed_at=executed_at,
                    user_id=user_id,
                )
                self._storage.record_execution(execution)
                return execution
        llm_client = self._llm_client
        if llm_client is None or not getattr(llm_client, "api_key", None):
            result = "LLM не настроен: PERPLEXITY_API_KEY"
            status = "error"
        else:
            llm_config = self._config.get("llm", {})
            model = llm_config.get("model", "sonar")
            system_prompt = llm_config.get("system_prompt")
            if mode == "search":
                system_prompt = llm_config.get("search_system_prompt", system_prompt)
            messages: list[dict[str, Any]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
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
            messages.append({"role": "user", "content": prompt})
            try:
                response = await llm_client.create_chat_completion(
                    model=model,
                    messages=messages,
                )
                result = response.get("content", "")
                if mode == "search":
                    citations = response.get("citations") or []
                    if citations:
                        lines = ["Источники:"]
                        for index, url in enumerate(citations, start=1):
                            lines.append(f"{index}) {url}")
                        result = f"{result}\n\n" + "\n".join(lines) if result else "\n".join(lines)
                status = "success"
            except Exception as exc:
                result = str(exc)
                status = "error"
                LOGGER.warning("LLM request failed: %s", exc, exc_info=True)

        execution = TaskExecutionResult(
            task_name=mode,
            payload=prompt,
            result=result,
            status=status,
            executed_at=executed_at,
            user_id=user_id,
        )
        self._storage.record_execution(execution)
        return execution

    async def search_llm(self, user_id: int, prompt: str) -> TaskExecutionResult:
        return await self.ask_llm(user_id, prompt, mode="search")

    async def handle_text(self, user_id: int, text: str) -> TaskExecutionResult:
        allowed, error_message = self._ensure_allowed(user_id)
        if not allowed:
            execution = TaskExecutionResult(
                task_name="text",
                payload=text,
                result=error_message,
                status="error",
                executed_at=datetime.now(timezone.utc),
                user_id=user_id,
            )
            self._storage.record_execution(execution)
            return execution

        trimmed = text.strip()
        lower = trimmed.lower()
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
            return self.execute_task(user_id, task_name, task_payload)

        if lower.startswith("search ") or lower.startswith("search:"):
            payload = trimmed[7:].strip()
            if not payload:
                execution = TaskExecutionResult(
                    task_name="search",
                    payload="",
                    result="Введите текст поиска. Пример: search Новости",
                    status="error",
                    executed_at=datetime.now(timezone.utc),
                    user_id=user_id,
                )
                self._storage.record_execution(execution)
                return execution
            return await self.search_llm(user_id, payload)

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


def load_orchestrator_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        LOGGER.warning("Orchestrator config not found at %s. Using defaults.", path)
        return {}
    with path.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)
