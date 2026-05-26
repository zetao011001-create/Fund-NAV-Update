"""Centralized loguru configuration."""

from __future__ import annotations

import sys
from loguru import logger
from loguru._logger import Logger

from .config import LOG_DIR, LOG_LEVEL

_CONFIGURED = False


def setup_logger() -> Logger:
    """Configure stdout + rotating file sinks. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        return logger

    logger.remove()
    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}:{function}:{line}</cyan> | "
        "<level>{message}</level>"
    )
    logger.add(sys.stdout, level=LOG_LEVEL, format=fmt, colorize=True)
    logger.add(
        LOG_DIR / "fund_nav_{time:YYYY-MM-DD}.log",
        level=LOG_LEVEL,
        rotation="00:00",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
    )

    _CONFIGURED = True
    return logger


log = setup_logger()
