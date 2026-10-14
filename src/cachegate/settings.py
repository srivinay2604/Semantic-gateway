"""Application settings loaded from environment variables via pydantic-settings.

All configuration for CacheGate lives here. Values can be set via environment
variables or a .env file. See .env.example for the full list.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """CacheGate runtime settings.

    All fields correspond to environment variables documented in .env.example
    and Section 13 of the project spec.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Application
    # -------------------------------------------------------------------------
    app_env: str = Field(default="dev", description="Environment: dev | staging | prod")
    log_level: str = Field(default="INFO", description="Logging level")
    admin_api_key: str = Field(
        default="change-me",
        description="Admin API key (used in Phase 6 auth). Never hardcode real values.",
    )

    # -------------------------------------------------------------------------
    # Redis
    # -------------------------------------------------------------------------
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL",
    )

    # -------------------------------------------------------------------------
    # Provider settings
    # -------------------------------------------------------------------------
    openai_api_key: str = Field(default="", description="OpenAI API key (Phase 2)")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1", description="OpenAI API base URL"
    )
    anthropic_api_key: str = Field(default="", description="Anthropic API key (Phase 2)")
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com/v1", description="Anthropic API base URL"
    )
    provider_timeout_seconds: float = Field(
        default=30.0, description="Upstream provider request timeout"
    )
    provider_max_retries: int = Field(default=2, description="Max retries per provider call")

    # Mock provider
    mock_latency_ms: int = Field(default=800, description="Mock provider base latency (ms)")
    mock_latency_jitter_ms: int = Field(default=200, description="Mock provider latency jitter ±ms")
    mock_error_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Mock provider simulated error rate [0, 1]",
    )

    # -------------------------------------------------------------------------
    # Cache
    # -------------------------------------------------------------------------
    cache_enabled: bool = Field(default=True, description="Enable caching layer")
    cache_schema_version: str = Field(
        default="v1",
        description="Cache key schema version; bump to invalidate all entries",
    )
    cache_ttl_seconds: int = Field(default=86400, description="Default cache TTL (seconds)")
    cache_max_temperature: float = Field(
        default=0.3,
        description="Requests with temperature above this are not cached",
    )
    semantic_threshold: float = Field(
        default=0.92,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity for a semantic cache hit",
    )
    semantic_top_k: int = Field(
        default=3, description="Number of KNN candidates retrieved for semantic search"
    )
    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description="Embedding model name (Phase 5)",
    )
    embedding_dim: int = Field(default=384, description="Embedding vector dimension (Phase 5)")

    # -------------------------------------------------------------------------
    # Rate limiting
    # -------------------------------------------------------------------------
    rate_limit_default_rps: float = Field(
        default=5.0, description="Default token bucket refill rate (tokens/second)"
    )
    rate_limit_default_burst: int = Field(
        default=20, description="Default token bucket burst capacity"
    )
    rate_limit_fail_open: bool = Field(
        default=True,
        description="If True, allow requests through when Redis is unreachable",
    )

    # -------------------------------------------------------------------------
    # Resilience
    # -------------------------------------------------------------------------
    breaker_failure_threshold: int = Field(
        default=5, description="Number of failures before circuit opens (Phase 3)"
    )
    breaker_recovery_seconds: int = Field(
        default=30, description="Seconds before circuit moves to half-open (Phase 3)"
    )

    # -------------------------------------------------------------------------
    # Hardening & Limits (Phase 7)
    # -------------------------------------------------------------------------
    max_request_size_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Maximum request payload size in bytes (default 10MB)",
    )
    singleflight_enabled: bool = Field(
        default=True,
        description="Enable single-flight request coalescing for identical requests",
    )
    models_config_path: str = Field(
        default="config/models.yaml",
        description="Path to model alias config YAML",
    )
    pricing_config_path: str = Field(
        default="config/pricing.yaml",
        description="Path to pricing config YAML",
    )


# Module-level singleton loaded once at import time.
# Tests can override by patching cachegate.settings.settings.
settings = Settings()
