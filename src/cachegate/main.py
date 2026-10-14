"""FastAPI application factory for CacheGate.

The ``app`` object is the ASGI entry point. It is created by :func:`create_app`
and exposed as a module-level singleton for uvicorn.

Lifespan:
  - Configures structured logging.
  - Loads the ProviderRouter from config/models.yaml.
  - (Phase 2+) Connects to Redis.
  - (Phase 5+) Pre-loads the embedding model.

Middleware stack (inner to outer):
  1. MetricsMiddleware    — records Prometheus counters/histograms + access log
  2. RequestIdMiddleware  — assigns/propagates X-Request-Id, binds to log ctx

Routers:
  - /healthz, /readyz
  - /v1/models
  - /v1/chat/completions
  - /metrics (Prometheus ASGI app, mounted directly)
  - /admin/* (Phase 6 placeholder)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError

from cachegate.api import admin, chat, health, models
from cachegate.middleware.metrics import MetricsMiddleware
from cachegate.middleware.request_id import RequestIdMiddleware
from cachegate.observability.logging import configure_logging
from cachegate.providers.router import ProviderRouter
from cachegate.schemas.openai import OpenAIErrorResponse
from cachegate.settings import settings

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown logic."""
    # ---- Startup ----
    configure_logging()

    logger.info(
        "cachegate_starting",
        version="0.1.0",
        env=settings.app_env,
        log_level=settings.log_level,
    )

    # Load provider router (reads config/models.yaml)
    app.state.provider_router = ProviderRouter(settings.models_config_path)

    # Phase 2+: connect Redis
    # Phase 5+: pre-load embedding model

    logger.info("cachegate_ready", host="0.0.0.0", port=8000)

    yield  # Application runs here

    # ---- Shutdown ----
    logger.info("cachegate_shutting_down")
    # Phase 2+: close Redis connection pool
    # Phase 5+: unload embedding model


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        A configured :class:`fastapi.FastAPI` instance.
    """
    application = FastAPI(
        title="CacheGate",
        description=(
            "OpenAI-compatible LLM gateway with exact + semantic caching, "
            "multi-provider fallback, rate limiting, and observability."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ---- Middleware (order: last added = outermost) ----
    application.add_middleware(MetricsMiddleware)
    application.add_middleware(RequestIdMiddleware)

    # ---- Routers ----
    application.include_router(health.router)
    application.include_router(models.router)
    application.include_router(chat.router)
    application.include_router(admin.router)

    # ---- Prometheus /metrics ----
    @application.get("/metrics", include_in_schema=False)
    @application.get("/metrics/", include_in_schema=False)
    def metrics_endpoint() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # ---- Global exception handlers ----

    @application.exception_handler(ValidationError)
    async def validation_exception_handler(_request: object, exc: ValidationError) -> JSONResponse:
        error = OpenAIErrorResponse.bad_request(str(exc))
        return JSONResponse(status_code=400, content=error.model_dump())

    return application


# Module-level singleton used by uvicorn
app = create_app()
