"""Metrics middleware.

Records per-request Prometheus metrics:
- ``cg_requests_total`` (counter) labelled by route and HTTP status.
- ``cg_request_duration_seconds`` (histogram) labelled by route.

Also emits a structured log line per request with route, status, and latency.

Added to the FastAPI app in ``main.py`` after RequestIdMiddleware.
"""

from __future__ import annotations

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match

from cachegate.observability.metrics import request_duration_seconds, requests_total

logger = structlog.get_logger(__name__)


def _route_label(request: Request) -> str:
    """Return a stable route template string like ``/v1/chat/completions``.

    Falls back to the raw path if no route matches (e.g., 404).
    """
    for route in request.app.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            return getattr(route, "path", request.url.path)
    return request.url.path


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record Prometheus metrics and a JSON log line for every HTTP request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        route = _route_label(request)
        t0 = time.monotonic()

        response = await call_next(request)

        latency = time.monotonic() - t0
        status = str(response.status_code)

        # Update Prometheus
        requests_total.labels(route=route, status=status).inc()
        request_duration_seconds.labels(route=route).observe(latency)

        # Structured access log
        logger.info(
            "http_request",
            route=route,
            method=request.method,
            status=status,
            latency_ms=round(latency * 1000, 1),
        )

        return response
