from __future__ import annotations

import json

from app.core.models import TaskDefinition
from app.core.result import OrchestratorResult, ok


class TaskError(Exception):
    """Base error for task execution."""


class InvalidPayloadError(TaskError):
    """Raised when task payload cannot be processed."""


def task_echo(payload: str) -> OrchestratorResult:
    return ok(payload, intent="task.echo", mode="tool")


def task_upper(payload: str) -> OrchestratorResult:
    return ok(payload.upper(), intent="task.upper", mode="tool")


def task_json_pretty(payload: str) -> OrchestratorResult:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise InvalidPayloadError("Payload is not valid JSON.") from exc
    return ok(
        json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True),
        intent="task.json_pretty",
        mode="tool",
    )


def get_task_registry() -> dict[str, TaskDefinition]:
    # TODO: C extension tasks can be registered here (e.g. via ctypes or a custom Python module).
    return {
        "echo": TaskDefinition(
            name="echo",
            description="Return payload as-is.",
            handler=task_echo,
        ),
        "upper": TaskDefinition(
            name="upper",
            description="Uppercase text payload.",
            handler=task_upper,
        ),
        "json_pretty": TaskDefinition(
            name="json_pretty",
            description="Pretty-print JSON payload.",
            handler=task_json_pretty,
        ),
    }
