"""Admin API endpoints for key management, cache flushing, and usage statistics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from cachegate.middleware.auth import KeyStore
from cachegate.usage.tracker import UsageTracker

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateKeyRequest(BaseModel):
    name: str = Field(description="Human-readable identifier for the key")
    tenant_id: str = Field(default="default", description="Tenant partition identifier")
    rate_limit_rps: float = Field(default=5.0, description="Token bucket refill rate per second")
    rate_limit_burst: int = Field(default=20, description="Token bucket burst capacity")
    daily_budget_usd: float | None = Field(
        default=None, description="Optional daily cost limit in USD"
    )


@router.post("/keys", summary="Create a new API key")
async def create_api_key(request: Request, body: CreateKeyRequest) -> dict[str, Any]:
    key_store: KeyStore | None = getattr(request.app.state, "key_store", None)
    if key_store is None:
        raise HTTPException(status_code=500, detail="KeyStore is not initialized")

    raw_token, record = await key_store.create_key(
        name=body.name,
        tenant_id=body.tenant_id,
        rate_limit_rps=body.rate_limit_rps,
        rate_limit_burst=body.rate_limit_burst,
        daily_budget_usd=body.daily_budget_usd,
    )
    return {
        "message": (
            "API key created successfully. Save this secret token securely; "
            "it cannot be retrieved again."
        ),
        "key": raw_token,
        **record,
    }


@router.get("/keys", summary="List all configured API keys")
async def list_api_keys(request: Request) -> list[dict[str, Any]]:
    key_store: KeyStore | None = getattr(request.app.state, "key_store", None)
    if key_store is None:
        return []
    return await key_store.list_keys()


@router.delete("/keys/{key_id}", summary="Revoke an API key")
async def revoke_api_key(request: Request, key_id: str) -> dict[str, Any]:
    key_store: KeyStore | None = getattr(request.app.state, "key_store", None)
    if key_store is None:
        raise HTTPException(status_code=500, detail="KeyStore is not initialized")

    revoked = await key_store.revoke_key(key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"message": f"Key {key_id} successfully revoked", "active": False}


@router.delete("/cache", summary="Flush cache entries")
async def flush_cache(
    request: Request,
    tenant_id: str | None = None,
    all_entries: bool = False,
) -> dict[str, Any]:
    cache_store = getattr(request.app.state, "cache_store", None)
    if cache_store is None:
        return {"deleted": 0}

    if all_entries:
        pattern = "*"
    elif tenant_id:
        pattern = f"cg:*:*:{tenant_id}:*"
    else:
        pattern = "cg:*:exact:*"

    deleted = await cache_store.flush(pattern)
    return {"message": "Cache flushed successfully", "deleted": deleted, "pattern": pattern}


@router.get("/stats", summary="Get aggregated usage, cost, and savings statistics")
async def get_stats(request: Request) -> dict[str, Any]:
    usage_tracker: UsageTracker | None = getattr(request.app.state, "usage_tracker", None)
    if usage_tracker is None:
        return {"total_requests": 0, "total_hits": 0, "hit_rate": 0.0}
    return await usage_tracker.get_all_usage()
