"""Structured logging setup using structlog.

Call :func:`configure_logging` once at application startup (from
``main.py`` lifespan). After that, obtain a bound logger anywhere with::

    import structlog
    logger = structlog.get_logger(__name__)

The logger produces JSON-formatted output in non-dev environments and
colourised console output in dev mode.

Request-ID binding happens in the request-ID middleware via
``structlog.contextvars.bind_contextvars(request_id=...)``.
"""

from __future__ import annotations

import logging
import sys

import structlog

from cachegate.settings import settings


def configure_logging() -> None:
    """Configure structlog and the stdlib root logger.

    Must be called once before any log messages are emitted.
    """
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Shared processors for all log entries
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.app_env == "dev":
        # Human-readable coloured output for local development
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level)

    # Quieten noisy libraries
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
