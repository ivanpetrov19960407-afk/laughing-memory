from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.models import TaskExecutionResult
from app.core.tasks import InvalidPayloadError, TaskDefinition, TaskError, get_task_registry
from app.infra.llm import PerplexityClient
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
    ) -> None:
        self._config = config
        self._storage = storage
        self._registry = registry or get_task_registry()
        self._llm_client = llm_client

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    def list_tasks(self) -> list[TaskDefinition]:
        enabled = self._enabled_tasks()
        return [self._registry[name] for name in enabled if name in self._registry]

    def execute_task(self, user_id: int, task_name: str, payload: str) -> TaskExecutionResult:
        executed_at = datetime.now(timezone.utc)
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

    async def ask_llm(self, user_id: int, prompt: str) -> TaskExecutionResult:
        executed_at = datetime.now(timezone.utc)
        llm_client = self._llm_client
        if llm_client is None or not getattr(llm_client, "api_key", None):
            result = "LLM не настроен: PERPLEXITY_API_KEY"
            status = "error"
        else:
            llm_config = self._config.get("llm", {})
            model = llm_config.get("model", "sonar")
            system_prompt = llm_config.get("system_prompt")
            messages: list[dict[str, Any]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            try:
                response = await llm_client.create_chat_completion(
                    model=model,
                    messages=messages,
                )
                result = response.get("content", "")
                status = "success"
            except Exception as exc:
                result = str(exc)
                status = "error"
                LOGGER.warning("LLM request failed: %s", exc, exc_info=True)

        execution = TaskExecutionResult(
            task_name="ask",
            payload=prompt,
            result=result,
            status=status,
            executed_at=executed_at,
            user_id=user_id,
        )
        self._storage.record_execution(execution)
        return execution

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


def load_orchestrator_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        LOGGER.warning("Orchestrator config not found at %s. Using defaults.", path)
        return {}
    with path.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)
