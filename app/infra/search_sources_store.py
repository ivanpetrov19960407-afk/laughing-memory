"""Per-user overrides for search source enable/disable state."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def _default_path() -> Path:
    import os
    return Path(os.getenv("SEARCH_SOURCES_STORE_PATH", "data/search_sources.json"))


_LOCK = asyncio.Lock()


def _load_raw() -> dict:
    path = _default_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_raw(data: dict) -> None:
    path = _default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def get_disabled(user_id: int) -> set[str]:
    """Return set of source ids disabled by the user."""
    async with _LOCK:
        raw = _load_raw()
    key = str(user_id)
    user_data = raw.get(key)
    if not isinstance(user_data, dict):
        return set()
    disabled = user_data.get("disabled")
    if not isinstance(disabled, list):
        return set()
    return {str(x) for x in disabled if isinstance(x, str) and x.strip()}


async def set_disabled(user_id: int, source_id: str) -> bool:
    """Disable a source for the user. Returns True if state changed."""
    sid = source_id.strip()
    if not sid:
        return False
    async with _LOCK:
        raw = _load_raw()
        key = str(user_id)
        user_data = raw.setdefault(key, {"disabled": []})
        disabled = user_data.get("disabled")
        if not isinstance(disabled, list):
            disabled = []
            user_data["disabled"] = disabled
        if sid in disabled:
            return False
        disabled.append(sid)
        _save_raw(raw)
    return True


async def set_enabled(user_id: int, source_id: str) -> bool:
    """Enable a source for the user (remove from disabled). Returns True if state changed."""
    sid = source_id.strip()
    if not sid:
        return False
    async with _LOCK:
        raw = _load_raw()
        key = str(user_id)
        user_data = raw.get(key)
        if not isinstance(user_data, dict):
            return False
        disabled = user_data.get("disabled")
        if not isinstance(disabled, list) or sid not in disabled:
            return False
        disabled[:] = [x for x in disabled if x != sid]
        if not disabled:
            raw.pop(key, None)
        else:
            user_data["disabled"] = disabled
        _save_raw(raw)
    return True


async def list_overrides(user_id: int) -> dict[str, bool]:
    """Return {source_id: enabled} for the user. Only overridden sources are included."""
    disabled = await get_disabled(user_id)
    return {} if not disabled else {sid: False for sid in disabled}
