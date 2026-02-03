from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


DecisionStatus = Literal["ok", "refused", "error"]


@dataclass(frozen=True)
class Decision:
    intent: str
    status: DecisionStatus
    reason: str | None = None
    mode: str | None = None
