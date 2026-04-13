"""
Structured logging configuration for teb.

Provides JSON-formatted log output for production environments
and human-readable output for development.

Usage in main.py lifespan:
    from teb.logging_config import configure_logging
    configure_logging()
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from teb import config


class StructuredFormatter(logging.Formatter):
    """JSON log formatter for production use.

    Each log line is a single JSON object with consistent fields:
    - timestamp, level, logger, message, plus any extras.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add standard context fields if present
        for key in ("user_id", "goal_id", "task_id", "request_id", "method", "path", "status_code", "duration_ms"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val

        # Add exception info
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": str(record.exc_info[1]),
            }

        return json.dumps(log_entry, default=str)


class HumanFormatter(logging.Formatter):
    """Human-readable formatter for development."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


def configure_logging() -> None:
    """Configure root logger based on TEB_ENV and TEB_LOG_LEVEL.

    Production: JSON output to stdout (machine-parseable).
    Development: Human-readable colored output.
    """
    log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    log_format = "json" if config.TEB_ENV == "production" else "text"

    # Allow explicit override
    env_format = __import__("os").getenv("TEB_LOG_FORMAT", "")
    if env_format in ("json", "text"):
        log_format = env_format

    root = logging.getLogger()
    root.setLevel(log_level)

    # Remove existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    if log_format == "json":
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(HumanFormatter())

    root.addHandler(handler)

    # Reduce noise from third-party loggers
    for noisy in ("httpx", "httpcore", "uvicorn.access", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
