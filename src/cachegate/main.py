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

from cachegate.api import admin, chat, health, models, ui
from cachegate.cache.manager import CacheManager
from cachegate.cache.single_flight import SingleFlight
from cachegate.cache.stores.base import ExactCacheStore, VectorStore
from cachegate.cache.stores.redis_store import RedisExactStore, RedisVectorStore
from cachegate.embeddings.base import BaseEmbedder
from cachegate.limits.token_bucket import MemoryTokenBucketRateLimiter
from cachegate.middleware.auth import AuthMiddleware, KeyStore
from cachegate.middleware.metrics import MetricsMiddleware
from cachegate.middleware.rate_limit import RateLimitMiddleware
from cachegate.middleware.request_id import RequestIdMiddleware
from cachegate.middleware.size_limit import RequestSizeLimitMiddleware
from cachegate.observability.logging import configure_logging
from cachegate.providers.router import ProviderRouter
from cachegate.schemas.openai import OpenAIErrorResponse
from cachegate.settings import settings
from cachegate.usage.tracker import UsageTracker

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

    # Initialize CacheManager with Exact and Vector Stores
    cache_store: ExactCacheStore
    vector_store: VectorStore
    embedder: BaseEmbedder

    if settings.app_env == "test":
        from cachegate.cache.stores.memory_store import MemoryExactStore, MemoryVectorStore
        from cachegate.embeddings.sbert import MockEmbedder

        cache_store = MemoryExactStore()
        vector_store = MemoryVectorStore()
        embedder = MockEmbedder(dim=settings.embedding_dim)
    else:
        cache_store = RedisExactStore(redis_url=settings.redis_url)

        # 1. Resolve Embedder: SentenceTransformers -> Ollama nomic-embed-text -> MockEmbedder
        embedder = None
        try:
            import importlib

            importlib.import_module("sentence_transformers")
            from cachegate.embeddings.sbert import SentenceTransformerEmbedder

            embedder = SentenceTransformerEmbedder(
                model_name=settings.embedding_model,
                dim=settings.embedding_dim,
            )
            logger.info("using_sentence_transformers_embedder", model=settings.embedding_model)
        except Exception:
            embedder = None

        if embedder is None:
            try:
                import httpx

                base_url = getattr(settings, "ollama_base_url", "http://localhost:11434")
                clean_url = base_url.rstrip("/")
                if clean_url.endswith("/v1"):
                    clean_url = clean_url[:-3]
                async with httpx.AsyncClient(timeout=1.5) as client:
                    resp = await client.get(f"{clean_url}/api/tags")
                    if resp.status_code == 200 and "nomic-embed-text" in resp.text:
                        from cachegate.embeddings.ollama_embed import OllamaEmbedder

                        embedder = OllamaEmbedder(
                            base_url=clean_url,
                            model="nomic-embed-text",
                            dim=768,
                        )
                        logger.info("using_ollama_embedder", model="nomic-embed-text", dim=768)
            except Exception:
                embedder = None

        if embedder is None:
            from cachegate.embeddings.sbert import MockEmbedder

            embedder = MockEmbedder(dim=settings.embedding_dim)
            logger.info("using_mock_embedder", dim=settings.embedding_dim)

        # 2. Resolve Vector Store: Redis with RediSearch -> MemoryVectorStore fallback
        vector_store = RedisVectorStore(
            redis_url=settings.redis_url,
            dim=embedder.dim,
        )
        try:
            await vector_store.setup_index()
        except Exception as exc:
            logger.warning(
                "redis_vector_index_unavailable_using_memory_store",
                error=str(exc),
            )
            from cachegate.cache.stores.memory_store import MemoryVectorStore

            vector_store = MemoryVectorStore()

    app.state.cache_store = cache_store
    app.state.vector_store = vector_store
    app.state.embedder = embedder
    app.state.cache_manager = CacheManager(
        exact_store=cache_store,
        vector_store=vector_store,
        embedder=embedder,
        settings=settings,
    )
    redis_client = getattr(cache_store, "_client", None)
    app.state.key_store = KeyStore(redis_client=redis_client)
    app.state.usage_tracker = UsageTracker(redis_client=redis_client)
    app.state.rate_limiter = MemoryTokenBucketRateLimiter()
    app.state.single_flight = SingleFlight(redis_client=redis_client)

    logger.info("cachegate_ready", host="0.0.0.0", port=8000)

    yield  # Application runs here

    # ---- Shutdown ----
    logger.info("cachegate_shutting_down")
    if hasattr(app.state, "cache_store") and app.state.cache_store is not None:
        try:
            await app.state.cache_store.close()
        except Exception:
            pass
    if hasattr(app.state, "vector_store") and app.state.vector_store is not None:
        try:
            await app.state.vector_store.close()
        except Exception:
            pass


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
    application.add_middleware(RateLimitMiddleware)
    application.add_middleware(AuthMiddleware)
    application.add_middleware(MetricsMiddleware)
    application.add_middleware(RequestSizeLimitMiddleware)
    application.add_middleware(RequestIdMiddleware)

    # ---- Routers ----
    application.include_router(ui.router)
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
