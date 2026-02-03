from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WizardState:
    wizard_id: str
    step: str
    data: dict[str, Any]
    started_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "wizard_id": self.wizard_id,
            "step": self.step,
            "data": self.data,
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> WizardState | None:
        if not isinstance(payload, dict):
            return None
        wizard_id = payload.get("wizard_id")
        step = payload.get("step")
        data = payload.get("data")
        started_at = payload.get("started_at")
        updated_at = payload.get("updated_at")
        if not isinstance(wizard_id, str) or not wizard_id.strip():
            return None
        if not isinstance(step, str) or not step.strip():
            return None
        if not isinstance(data, dict):
            data = {}
        started_dt = _parse_datetime(started_at)
        updated_dt = _parse_datetime(updated_at) or started_dt
        if started_dt is None or updated_dt is None:
            return None
        return WizardState(
            wizard_id=wizard_id,
            step=step,
            data=data,
            started_at=started_dt,
            updated_at=updated_dt,
        )


class WizardStore:
    def __init__(self, base_path: Path, *, timeout_seconds: int = 600) -> None:
        self._base_path = base_path
        self._timeout_seconds = max(60, int(timeout_seconds))

    @property
    def timeout_seconds(self) -> int:
        return self._timeout_seconds

    def load_state(
        self,
        *,
        user_id: int,
        chat_id: int,
        now: datetime | None = None,
    ) -> tuple[WizardState | None, bool]:
        path = self._state_path(user_id=user_id, chat_id=chat_id)
        if not path.exists():
            return None, False
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None, False
        state = WizardState.from_dict(payload)
        if state is None:
            return None, False
        current = now or datetime.now(timezone.utc)
        expired = (current - state.updated_at).total_seconds() > self._timeout_seconds
        if expired:
            self.clear_state(user_id=user_id, chat_id=chat_id)
            return None, True
        return state, False

    def save_state(self, *, user_id: int, chat_id: int, state: WizardState) -> None:
        path = self._state_path(user_id=user_id, chat_id=chat_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    def clear_state(self, *, user_id: int, chat_id: int) -> None:
        path = self._state_path(user_id=user_id, chat_id=chat_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def _state_path(self, *, user_id: int, chat_id: int) -> Path:
        filename = f"{chat_id}_{user_id}.json"
        return self._base_path / filename


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
