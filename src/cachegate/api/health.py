"""Health endpoints.

Phase 0: GET /healthz — liveness check (always 200 if process is running).
Phase 1+: GET /readyz — readiness (checks Redis connectivity and embedder).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Liveness response body."""

    status: str


@router.get("/healthz", response_model=HealthResponse, summary="Liveness check")
async def healthz() -> HealthResponse:
    """Return 200 OK if the process is alive.

    Does not check downstream dependencies — use /readyz for that.
    """
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=dict, summary="Readiness check")
async def readyz() -> dict[str, str]:
    """Return 200 if the gateway is ready to serve traffic.

    Phase 1: always returns ready.
    Phase 2+: will check Redis connectivity and embedder availability.
    """
    # TODO Phase 2: check Redis ping and embedder loaded state
    return {"status": "ready", "redis": "unchecked", "embedder": "unchecked"}
