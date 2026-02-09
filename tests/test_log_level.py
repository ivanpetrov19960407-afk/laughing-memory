"""Unit tests for LOG_LEVEL configuration (Infra-lite)."""
from __future__ import annotations

import logging

import pytest

from app.infra.config import get_log_level


def test_log_level_default_when_missing() -> None:
    """When LOG_LEVEL is not set, get_log_level returns INFO."""
    level = get_log_level(raw_env={})
    assert level == logging.INFO


def test_log_level_custom_valid() -> None:
    """When LOG_LEVEL is valid (e.g. DEBUG), get_log_level returns that level."""
    level = get_log_level(raw_env={"LOG_LEVEL": "DEBUG"})
    assert level == logging.DEBUG
    level = get_log_level(raw_env={"LOG_LEVEL": "WARNING"})
    assert level == logging.WARNING
