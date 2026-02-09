"""Unit tests for LOG_LEVEL from ENV (default and custom)."""
from __future__ import annotations

import logging

import pytest

from app.infra.config import DEFAULT_LOG_LEVEL, get_log_level


def test_log_level_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """When LOG_LEVEL is not set, default level (INFO) is returned."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    assert get_log_level(raw_env={}) == logging.INFO
    assert get_log_level(raw_env={}) == DEFAULT_LOG_LEVEL


def test_log_level_custom_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    """When LOG_LEVEL=DEBUG, returned level is DEBUG."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    env = {"LOG_LEVEL": "DEBUG"}
    assert get_log_level(raw_env=env) == logging.DEBUG
