"""Request payload size limit middleware.

Rejects requests exceeding max_request_size_bytes with HTTP 413 Payload Too Large.
Protects gateway against memory exhaustion and large unparseable payloads.
"""

from __future__ import annotations

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from cachegate.settings import settings

logger = structlog.get_logger(__name__)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Enforces upper limit on request body size."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        content_length_header = request.headers.get("content-length")
        if content_length_header is not None:
            try:
                content_length = int(content_length_header)
                if content_length > settings.max_request_size_bytes:
                    logger.warning(
                        "request_payload_too_large",
                        content_length=content_length,
                        max_allowed=settings.max_request_size_bytes,
                        path=request.url.path,
                    )
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "message": (
                                    f"Request payload size of {content_length} bytes exceeds "
                                    f"maximum allowed limit of "
                                    f"{settings.max_request_size_bytes} bytes."
                                ),
                                "type": "invalid_request_error",
                                "code": "payload_too_large",
                            }
                        },
                    )
            except ValueError:
                pass

        return await call_next(request)
