from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ActionLogEntry:
    id: int
    user_id: int
    ts: datetime
    action_type: str
    payload: dict[str, Any]
    correlation_id: str | None = None

    def to_summary(self) -> str:
        summary = self.payload.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        return self.action_type
