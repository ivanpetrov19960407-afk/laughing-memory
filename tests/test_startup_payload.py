from __future__ import annotations

import json

from app.infra.config import StartupFeatures
from app.main import _build_startup_integrations


def test_startup_integrations_excludes_google() -> None:
    integrations = _build_startup_integrations(
        StartupFeatures(caldav_enabled=True, llm_enabled=True)
    )

    assert "google" not in integrations
    payload = json.dumps({"integrations": integrations}, ensure_ascii=False)
    assert '"google"' not in payload


def test_startup_integrations_omit_disabled() -> None:
    integrations = _build_startup_integrations(
        StartupFeatures(caldav_enabled=False, llm_enabled=True)
    )

    assert "caldav" not in integrations
    assert integrations == {"llm": True}
