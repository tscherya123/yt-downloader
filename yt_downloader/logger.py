"""Centralized logging helpers for YT Downloader."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

LOG_DIR = Path.home() / "Documents" / "YT Downloader Settings"
LOG_FILE = LOG_DIR / "debug.log"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging() -> None:
    """Configure logging to write to both stdout and a debug log file."""

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(
        level=logging.DEBUG,
        format=LOG_FORMAT,
        handlers=handlers,
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    """Return a logger instance with the shared configuration."""

    return logging.getLogger(name)


__all__ = ["get_logger", "setup_logging"]
