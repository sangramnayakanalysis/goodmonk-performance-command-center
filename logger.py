"""
logger.py
=========
Central logging setup. Produces:
  - logs/execution.log  — everything, rotating
  - logs/error.log      — WARNING and above only
  - logs/api.log        — GTmetrix API request/response activity
  - logs/YYYY-MM-DD.log — a per-day log for quick "what happened today" review

All loggers also echo to stdout so GitHub Actions run logs show activity
live, not just after the fact.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

from config import LOGS_DIR

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-12s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _make_handler(path, level=logging.INFO) -> logging.Handler:
    handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    return handler


_configured = False


def setup_logging() -> None:
    """Idempotent — safe to call multiple times (e.g. from tests)."""
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    root.addHandler(console)

    root.addHandler(_make_handler(LOGS_DIR / "execution.log", logging.INFO))
    root.addHandler(_make_handler(LOGS_DIR / "error.log", logging.WARNING))

    today = datetime.now().strftime("%Y-%m-%d")
    root.addHandler(_make_handler(LOGS_DIR / f"{today}.log", logging.INFO))

    _configured = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)


def get_api_logger() -> logging.Logger:
    """Separate logger + file specifically for GTmetrix API traffic,
    so a busy run doesn't bury API-level detail in the general log."""
    setup_logging()
    logger = logging.getLogger("api")
    if not any(isinstance(h, RotatingFileHandler) and "api.log" in str(h.baseFilename) for h in logger.handlers):
        logger.addHandler(_make_handler(LOGS_DIR / "api.log", logging.DEBUG))
        logger.propagate = True
    return logger
