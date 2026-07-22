"""Loguru-based logging configured once at import time."""

from __future__ import annotations

import logging
import sys

from loguru import logger

from app.core.config import settings

_configured = False


class InterceptHandler(logging.Handler):
    """Route stdlib logging (uvicorn, sqlalchemy, celery) through Loguru."""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging() -> None:
    global _configured
    if _configured:
        return

    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>"
        ),
        backtrace=settings.app_env != "production",
        diagnose=settings.app_env != "production",
    )
    logger.add(
        "logs/app_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="14 days",
        level=settings.log_level,
        enqueue=True,
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine", "celery"):
        logging.getLogger(name).handlers = [InterceptHandler()]

    _configured = True
    logger.info("Logging configured (level={}, env={})", settings.log_level, settings.app_env)


__all__ = ["logger", "setup_logging"]
