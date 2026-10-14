"""Rate limiting middleware enforcing token bucket limits per API key."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from cachegate.settings import settings

logger = structlog.get_logger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforces token bucket rate limiting per key on LLM completion requests."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Only rate limit completion endpoints
        if not request.url.path.startswith("/v1/chat/completions"):
            return await call_next(request)

        rate_limiter = getattr(request.app.state, "rate_limiter", None)
        if rate_limiter is None:
            return await call_next(request)

        # Retrieve key_id set by AuthMiddleware or fallback to tenant/default
        key_id = getattr(request.state, "key_id", "default")
        rps = getattr(request.state, "rate_limit_rps", settings.rate_limit_default_rps)
        burst = getattr(request.state, "rate_limit_burst", settings.rate_limit_default_burst)

        allowed, remaining, retry_after = await rate_limiter.check(
            key_id=key_id,
            cost=1,
            rps=rps,
            burst=burst,
        )

        if not allowed:
            logger.warning(
                "rate_limit_exceeded",
                key_id=key_id,
                retry_after=retry_after,
            )
            err_body: dict[str, Any] = {
                "error": {
                    "message": "Rate limit exceeded. Please retry after the cooldown period.",
                    "type": "requests",
                    "param": None,
                    "code": "rate_limit_exceeded",
                }
            }
            return JSONResponse(
                status_code=429,
                content=err_body,
                headers={
                    "Retry-After": str(max(1, int(retry_after))),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(max(0, int(remaining)))
        return response
