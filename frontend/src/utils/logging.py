"""Simple application logging helpers."""

from __future__ import annotations

import logging


def get_logger(name: str) -> logging.Logger:
    """Return a console logger with a lightweight default format."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger
