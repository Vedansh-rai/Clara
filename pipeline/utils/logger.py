"""
logger.py — Structured logging utility for the Clara pipeline.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

console = Console()

_LOG_DIR = Path(os.getenv("LOGS_DIR", "logs"))


def get_logger(name: str, client_id: str | None = None) -> logging.Logger:
    """Return a configured logger that writes to console (Rich) and a file."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.DEBUG)

    # Console handler (Rich, coloured)
    console_handler = RichHandler(
        console=console,
        rich_tracebacks=True,
        markup=True,
        show_time=True,
    )
    console_handler.setLevel(logging.INFO)

    # File handler (plain JSON-compatible lines)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = f"_{client_id}" if client_id else ""
    log_file = _LOG_DIR / f"{ts}{suffix}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    )

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger


def log_event(logger: logging.Logger, event: str, **kwargs: object) -> None:
    """Log a structured event as a JSON blob (useful for audit trails)."""
    payload = {"event": event, "ts": datetime.now(timezone.utc).isoformat(), **kwargs}
    logger.debug(json.dumps(payload, default=str))
