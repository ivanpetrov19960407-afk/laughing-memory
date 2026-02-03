from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from app.core.result import OrchestratorResult


TaskCallable = Callable[[str], OrchestratorResult]


@dataclass(frozen=True)
class TaskDefinition:
    name: str
    description: str
    handler: TaskCallable


@dataclass(frozen=True)
class TaskExecutionResult:
    task_name: str
    payload: str
    result: str
    status: str
    executed_at: datetime
    user_id: int
