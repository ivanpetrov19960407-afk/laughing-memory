"""Tests for logging configuration (LOG_LEVEL, LOG_TO_FILE, LOG_FILE_PATH)."""

from __future__ import annotations

import logging

import pytest

from app.infra.logging_config import configure_logging


def test_log_level_defaults_to_info(monkeypatch) -> None:
    """Test that LOG_LEVEL defaults to INFO when not set."""
    # Remove LOG_LEVEL if it exists
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    
    root = logging.getLogger()
    configure_logging()
    
    assert root.level == logging.INFO


def test_log_level_from_env_debug(monkeypatch) -> None:
    """Test that LOG_LEVEL=DEBUG sets logger level to DEBUG."""
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    
    root = logging.getLogger()
    configure_logging()
    
    assert root.level == logging.DEBUG


def test_log_level_from_env_warning(monkeypatch) -> None:
    """Test that LOG_LEVEL=WARNING sets logger level to WARNING."""
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    
    root = logging.getLogger()
    configure_logging()
    
    assert root.level == logging.WARNING


def test_log_level_from_env_error(monkeypatch) -> None:
    """Test that LOG_LEVEL=ERROR sets logger level to ERROR."""
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    
    root = logging.getLogger()
    configure_logging()
    
    assert root.level == logging.ERROR


def test_log_level_invalid_falls_back_to_info(monkeypatch) -> None:
    """Test that invalid LOG_LEVEL falls back to INFO."""
    monkeypatch.setenv("LOG_LEVEL", "INVALID_LEVEL")
    
    root = logging.getLogger()
    configure_logging()
    
    assert root.level == logging.INFO


def test_log_level_case_insensitive(monkeypatch) -> None:
    """Test that LOG_LEVEL is case-insensitive."""
    monkeypatch.setenv("LOG_LEVEL", "debug")
    
    root = logging.getLogger()
    configure_logging()
    
    assert root.level == logging.DEBUG


def test_log_level_with_whitespace(monkeypatch) -> None:
    """Test that LOG_LEVEL with whitespace is handled correctly."""
    monkeypatch.setenv("LOG_LEVEL", "  DEBUG  ")
    
    root = logging.getLogger()
    configure_logging()
    
    assert root.level == logging.DEBUG
