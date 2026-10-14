"""Prometheus metrics for CacheGate.

Defines all metric objects used across the gateway. Import and call the
increment/observe helpers from route handlers and middleware.

Phase 1: cg_requests_total, cg_request_duration_seconds.
Phase 8: adds remaining metrics from spec §17.
"""

from __future__ import annotations

from prometheus_client import (
    Counter,
    Histogram,
    make_asgi_app,
)

# ---------------------------------------------------------------------------
# Phase 1 metrics
# ---------------------------------------------------------------------------

requests_total = Counter(
    "cg_requests_total",
    "Total HTTP requests processed by the gateway",
    labelnames=["route", "status"],
)

request_duration_seconds = Histogram(
    "cg_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=["route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# ---------------------------------------------------------------------------
# Phase 2+ metrics (stubs — labels populated when phases are implemented)
# ---------------------------------------------------------------------------

provider_requests_total = Counter(
    "cg_provider_requests_total",
    "Total requests forwarded to upstream providers",
    labelnames=["provider", "status"],
)

provider_duration_seconds = Histogram(
    "cg_provider_duration_seconds",
    "Upstream provider call duration in seconds",
    labelnames=["provider"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

# Phase 4+ — cache metrics
cache_hits_total = Counter(
    "cg_cache_hits_total",
    "Total cache hits",
    labelnames=["tier", "tenant"],
)

cache_lookup_duration_seconds = Histogram(
    "cg_cache_lookup_duration_seconds",
    "Cache lookup duration in seconds",
    labelnames=["tier"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1),
)

# Phase 6+ — rate limiting
rate_limited_total = Counter(
    "cg_rate_limited_total",
    "Total requests rejected by rate limiting",
    labelnames=["key_id"],
)

cache_errors_total = Counter(
    "cg_cache_errors_total",
    "Total cache operation errors",
    labelnames=["operation"],
)

tokens_total = Counter(
    "cg_tokens_total",
    "Total tokens processed",
    labelnames=["type", "model", "key_id"],
)

cost_usd_total = Counter(
    "cg_cost_usd_total",
    "Total estimated cost in USD",
    labelnames=["model", "key_id"],
)

cost_saved_usd_total = Counter(
    "cg_cost_saved_usd_total",
    "Total estimated cost saved by cache hits in USD",
    labelnames=["model", "key_id"],
)

circuit_breaker_state = Counter(
    "cg_circuit_breaker_state_total",
    "Circuit breaker state change events",
    labelnames=["provider", "state"],
)

singleflight_coalesced_total = Counter(
    "cg_singleflight_coalesced_total",
    "Total requests coalesced by single-flight (Phase 7)",
)

# ---------------------------------------------------------------------------
# ASGI app for /metrics endpoint (mounts on the FastAPI app)
# ---------------------------------------------------------------------------

metrics_app = make_asgi_app()
