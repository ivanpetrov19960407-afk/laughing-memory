from __future__ import annotations

import os


def resolve_app_version(metadata: dict[str, object] | None = None) -> str:
    for key in ("APP_VERSION", "GIT_SHA", "BUILD_ID"):
        value = os.getenv(key)
        if value:
            return value.strip()
    if isinstance(metadata, dict):
        version = metadata.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    return "unknown"
