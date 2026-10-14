"""Request-ID middleware.

Assigns a UUID to every incoming request and propagates it:
- Accepts an incoming ``X-Request-Id`` header if present (idempotent for
  clients that assign their own IDs).
- Returns the assigned ID in the ``X-Request-Id`` response header.
- Binds it to the structlog context so every log line within the request
  carries ``request_id`` automatically.

Usage: added to the FastAPI app in ``main.py``.
"""

from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Generate or propagate X-Request-Id and bind it to the log context."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Accept an existing request ID from the client or generate a fresh one
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())

        # Bind to structlog context vars so all log lines in this request carry it
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Stash on the request state so route handlers can read it
        request.state.request_id = request_id

        response = await call_next(request)

        # Propagate back to client
        response.headers["X-Request-Id"] = request_id
        return response
