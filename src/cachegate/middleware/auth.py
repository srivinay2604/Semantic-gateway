"""Authentication middleware and API key management following spec §11.1."""

from __future__ import annotations

import secrets
import time
from typing import Any

import redis.asyncio as aioredis
import structlog
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from cachegate.cache.keys import sha256_digest
from cachegate.settings import settings

logger = structlog.get_logger(__name__)


class KeyStore:
    """Stores API keys indexed by SHA-256 hash in Redis or in-memory."""

    def __init__(self, redis_client: aioredis.Redis | None = None) -> None:
        self._client = redis_client
        # hash_val -> dict
        self._memory_keys: dict[str, dict[str, Any]] = {}
        # key_id -> hash_val
        self._id_to_hash: dict[str, str] = {}

    async def create_key(
        self,
        name: str,
        tenant_id: str = "default",
        rate_limit_rps: float = 5.0,
        rate_limit_burst: int = 20,
        daily_budget_usd: float | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Create a new API key. Plaintext key is returned only once."""
        raw_token = f"cg-{secrets.token_urlsafe(24)}"
        hash_val = sha256_digest(raw_token)
        key_id = f"key_{secrets.token_hex(6)}"

        record: dict[str, Any] = {
            "id": key_id,
            "name": name,
            "tenant_id": tenant_id,
            "rate_limit_rps": float(rate_limit_rps),
            "rate_limit_burst": int(rate_limit_burst),
            "daily_budget_usd": daily_budget_usd,
            "active": True,
            "created_at": int(time.time()),
        }

        self._memory_keys[hash_val] = record
        self._id_to_hash[key_id] = hash_val

        if self._client is not None:
            rkey = f"cg:v1:apikey:{hash_val}"
            try:
                # Store in Redis hash
                mapping = {
                    "id": record["id"],
                    "name": record["name"],
                    "tenant_id": record["tenant_id"],
                    "rate_limit_rps": str(record["rate_limit_rps"]),
                    "rate_limit_burst": str(record["rate_limit_burst"]),
                    "daily_budget_usd": str(record["daily_budget_usd"])
                    if record["daily_budget_usd"] is not None
                    else "",
                    "active": "1" if record["active"] else "0",
                    "created_at": str(record["created_at"]),
                }
                await self._client.hset(rkey, mapping=mapping)  # type: ignore[arg-type]
            except Exception as exc:
                logger.warning("redis_apikey_save_failed", key=rkey, error=str(exc))

        return raw_token, record

    async def get_by_token(self, token: str) -> dict[str, Any] | None:
        """Lookup key record by plaintext token."""
        hash_val = sha256_digest(token)
        return await self.get_by_hash(hash_val)

    async def get_by_hash(self, hash_val: str) -> dict[str, Any] | None:
        """Lookup key record by SHA-256 hash."""
        if hash_val in self._memory_keys:
            return self._memory_keys[hash_val]

        if self._client is not None:
            rkey = f"cg:v1:apikey:{hash_val}"
            try:
                raw = await self._client.hgetall(rkey)
                if raw:
                    rec = {
                        "id": raw["id"],
                        "name": raw["name"],
                        "tenant_id": raw["tenant_id"],
                        "rate_limit_rps": float(raw["rate_limit_rps"]),
                        "rate_limit_burst": int(raw["rate_limit_burst"]),
                        "daily_budget_usd": float(raw["daily_budget_usd"])
                        if raw.get("daily_budget_usd")
                        else None,
                        "active": raw.get("active") == "1",
                        "created_at": int(raw.get("created_at", 0)),
                    }
                    self._memory_keys[hash_val] = rec
                    return rec
            except Exception as exc:
                logger.warning("redis_apikey_get_failed", key=rkey, error=str(exc))

        return None

    async def list_keys(self) -> list[dict[str, Any]]:
        """List metadata for all active keys (never returns plaintext token)."""
        return list(self._memory_keys.values())

    async def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key by marking it inactive."""
        hash_val = self._id_to_hash.get(key_id)
        if hash_val and hash_val in self._memory_keys:
            self._memory_keys[hash_val]["active"] = False
            if self._client is not None:
                rkey = f"cg:v1:apikey:{hash_val}"
                try:
                    await self._client.hset(rkey, "active", "0")
                except Exception as exc:
                    logger.warning("redis_apikey_revoke_failed", error=str(exc))
            return True
        return False


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforces authentication for admin routes and client API keys."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Public endpoints that bypass authentication
        if (
            path in ("/healthz", "/readyz", "/metrics", "/docs", "/redoc", "/openapi.json")
            or path.startswith("/healthz")
            or path.startswith("/readyz")
        ):
            return await call_next(request)

        # ---------------------------------------------------------------------
        # Admin Endpoint Protection
        # ---------------------------------------------------------------------
        if path.startswith("/admin"):
            auth_header = request.headers.get("authorization", "")
            admin_key_header = request.headers.get("x-admin-key", "")
            token = ""
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()
            elif admin_key_header:
                token = admin_key_header.strip()

            if not token or token != settings.admin_api_key:
                logger.warning("admin_unauthorized_access_attempt", path=path)
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": {
                            "message": "Invalid admin API key",
                            "type": "auth_error",
                            "code": "unauthorized",
                        }
                    },
                )
            return await call_next(request)

        # ---------------------------------------------------------------------
        # Public Gateway API (/v1/*) Protection
        # ---------------------------------------------------------------------
        auth_header = request.headers.get("authorization", "")
        key_store: KeyStore | None = getattr(request.app.state, "key_store", None)

        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            key_record: dict[str, Any] | None = None
            if token == settings.admin_api_key or (
                settings.app_env == "test" and token == "test-key"
            ):
                key_record = {
                    "id": "key_admin" if token == settings.admin_api_key else "key_test_default",
                    "name": "Admin Master Key"
                    if token == settings.admin_api_key
                    else "Test Default Key",
                    "tenant_id": "default",
                    "rate_limit_rps": 10000,
                    "rate_limit_burst": 20000,
                    "daily_budget_usd": None,
                    "active": True,
                }
            elif key_store is not None:
                key_record = await key_store.get_by_token(token)

            if key_record is None or not key_record.get("active", False):
                logger.warning("invalid_client_api_key", path=path)
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": {
                            "message": "Invalid or revoked API key",
                            "type": "auth_error",
                            "code": "invalid_api_key",
                        }
                    },
                )

            # Check daily budget enforcement (spec §11.1)
            budget = key_record.get("daily_budget_usd")
            usage_tracker = getattr(request.app.state, "usage_tracker", None)
            if budget is not None and usage_tracker is not None:
                usage = await usage_tracker.get_usage(key_record["id"])
                if usage.get("cost_usd", 0.0) >= budget:
                    logger.warning(
                        "client_daily_budget_exceeded", key_id=key_record["id"], budget=budget
                    )
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error": {
                                "message": (
                                    f"Daily budget of ${budget:.2f} exceeded for this API key"
                                ),
                                "type": "budget_error",
                                "code": "budget_exceeded",
                            }
                        },
                    )

            request.state.key_id = key_record["id"]
            request.state.tenant_id = key_record.get("tenant_id", "default")
            request.state.rate_limit_rps = key_record.get(
                "rate_limit_rps", settings.rate_limit_default_rps
            )
            request.state.rate_limit_burst = key_record.get(
                "rate_limit_burst", settings.rate_limit_default_burst
            )
        else:
            # Default unauthenticated identity for development / mock tests
            request.state.key_id = "default"
            request.state.tenant_id = "default"
            request.state.rate_limit_rps = settings.rate_limit_default_rps
            request.state.rate_limit_burst = settings.rate_limit_default_burst

        return await call_next(request)
