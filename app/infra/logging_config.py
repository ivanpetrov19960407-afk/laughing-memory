"""Centralized logging configuration for the bot.

Single entry point: configure_logging() sets root logger level, format,
optional file handler with rotation, and suppresses noisy third-party loggers.
LOG_LEVEL and LOG_FILE are read from environment; no secrets are logged.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Keys that suggest secret data; values are redacted in log formatters if we add one.
_SECRET_SUBSTRINGS = ("token", "password", "api_key", "secret", "authorization")

_DEFAULT_LEVEL = "INFO"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 3


def _get_level() -> int:
    raw = os.environ.get("LOG_LEVEL", _DEFAULT_LEVEL).strip().upper()
    return getattr(logging, raw, logging.INFO)


def _get_log_file() -> str | None:
    path = os.environ.get("LOG_FILE", "").strip()
    if not path:
        return None
    return path


def configure_logging(*, level: int | None = None, log_file: str | None = None) -> None:
    """Configure process-wide logging.

    Call once at startup. Sets root logger level and format; optionally
    adds a rotating file handler. Reduces noise from httpx/telegram/httpcore.

    Args:
        level: Log level (e.g. logging.INFO). If None, taken from LOG_LEVEL env.
        log_file: If set, log to this file with rotation. If None, from LOG_FILE env.
    """
    if level is None:
        level = _get_level()
    if log_file is None:
        log_file = _get_log_file()

    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=date_fmt)

    root = logging.getLogger()
    root.setLevel(level)
    # Remove any existing handlers so we don't duplicate (e.g. in tests).
    for h in root.handlers[:]:
        root.removeHandler(h)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file:
        try:
            path = Path(log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError as e:
            root.warning("Could not open log file %s: %s; logging to stderr only", log_file, e)

    # Reduce noise from third-party libs
    for name in ("httpx", "httpcore", "telegram", "telegram.ext"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        logger.propagate = True
    # Optional: disable very chatty HTTP logs entirely
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).disabled = True
        logging.getLogger(name).propagate = False


def log_exception(logger: logging.Logger, msg: str, *args: object, **kwargs: object) -> None:
    """Log an exception with full traceback.

    Use this instead of logger.exception when you want to ensure the
    traceback is always recorded (e.g. in startup code).
    """
    logger.exception(msg, *args, **kwargs)
