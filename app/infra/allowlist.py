from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AllowlistSnapshot:
    allowed_user_ids: list[int]
    updated_at: str


class AllowlistStore:
    def __init__(self, path: Path, initial_user_ids: set[int]) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._allowed_user_ids = set(initial_user_ids)
        self._updated_at = _now_iso()

    @property
    def path(self) -> Path:
        return self._path

    def is_allowed(self, user_id: int) -> bool:
        return user_id in self._allowed_user_ids

    def snapshot(self) -> AllowlistSnapshot:
        return AllowlistSnapshot(
            allowed_user_ids=sorted(self._allowed_user_ids),
            updated_at=self._updated_at,
        )

    async def load(self) -> None:
        async with self._lock:
            if not self._path.exists():
                await self._persist()
                return
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                allowed = data.get("allowed_user_ids", [])
                if not isinstance(allowed, list):
                    raise ValueError("allowed_user_ids must be a list")
                self._allowed_user_ids = {int(item) for item in allowed}
                updated_at = data.get("updated_at")
                if isinstance(updated_at, str) and updated_at:
                    self._updated_at = updated_at
            except Exception:
                LOGGER.exception("Failed to load allowlist from %s; keeping current list", self._path)

    async def add(self, user_id: int) -> bool:
        async with self._lock:
            if user_id in self._allowed_user_ids:
                return False
            self._allowed_user_ids.add(user_id)
            self._updated_at = _now_iso()
            await self._persist()
            return True

    async def remove(self, user_id: int) -> bool:
        async with self._lock:
            if user_id not in self._allowed_user_ids:
                return False
            self._allowed_user_ids.remove(user_id)
            self._updated_at = _now_iso()
            await self._persist()
            return True

    async def _persist(self) -> None:
        payload = {
            "allowed_user_ids": sorted(self._allowed_user_ids),
            "updated_at": self._updated_at,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, self._path)


def extract_allowed_user_ids(config: dict[str, Any]) -> set[int]:
    access_config = config.get("access", {}) if isinstance(config, dict) else {}
    allowed = access_config.get("allowed_user_ids", [])
    if not isinstance(allowed, list):
        return set()
    return {int(item) for item in allowed}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
