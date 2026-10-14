"""Token bucket rate limiter using Lua script for Redis and in-memory clock support."""

from __future__ import annotations

import time
from collections.abc import Callable

import redis.asyncio as aioredis
import structlog

from cachegate.cache.keys import rate_limit_key
from cachegate.limits.base import BaseRateLimiter

logger = structlog.get_logger(__name__)

LUA_TOKEN_BUCKET = """
local key = KEYS[1]
local cost = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local capacity = tonumber(ARGV[3])
local now = tonumber(ARGV[4])

local data = redis.call("HMGET", key, "tokens", "last_refill")
local tokens = tonumber(data[1])
local last_refill = tonumber(data[2])

if tokens == nil or last_refill == nil then
    tokens = capacity
    last_refill = now
else
    local elapsed = math.max(0, now - last_refill)
    tokens = math.min(capacity, tokens + elapsed * rate)
    last_refill = now
end

if tokens >= cost then
    tokens = tokens - cost
    redis.call("HSET", key, "tokens", tokens, "last_refill", last_refill)
    redis.call("EXPIRE", key, math.ceil(capacity / rate) + 60)
    return {1, string.format("%.4f", tokens), "0"}
else
    local needed = cost - tokens
    local retry_after = needed / rate
    redis.call("HSET", key, "tokens", tokens, "last_refill", last_refill)
    redis.call("EXPIRE", key, math.ceil(capacity / rate) + 60)
    return {0, string.format("%.4f", tokens), string.format("%.4f", retry_after)}
end
"""


class MemoryTokenBucketRateLimiter(BaseRateLimiter):
    """In-memory thread-safe token bucket rate limiter supporting fake clocks."""

    def __init__(self, clock_fn: Callable[[], float] | None = None) -> None:
        self._clock_fn = clock_fn or time.monotonic
        # key_id -> (tokens: float, last_refill: float)
        self._buckets: dict[str, tuple[float, float]] = {}

    async def check(
        self,
        key_id: str,
        cost: int = 1,
        rps: float = 5.0,
        burst: int = 20,
    ) -> tuple[bool, float, float]:
        now = self._clock_fn()
        bucket = self._buckets.get(key_id)

        if bucket is None:
            tokens = float(burst)
            last_refill = now
        else:
            tokens, last_refill = bucket
            elapsed = max(0.0, now - last_refill)
            tokens = min(float(burst), tokens + elapsed * rps)
            last_refill = now

        if tokens >= cost:
            tokens -= cost
            self._buckets[key_id] = (tokens, last_refill)
            return True, tokens, 0.0

        needed = cost - tokens
        retry_after = needed / rps
        self._buckets[key_id] = (tokens, last_refill)
        return False, tokens, retry_after


class RedisTokenBucketRateLimiter(BaseRateLimiter):
    """Redis-backed atomic token bucket rate limiter using Lua script."""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        fail_open: bool = True,
    ) -> None:
        self._client = redis_client
        self._fail_open = fail_open

    async def check(
        self,
        key_id: str,
        cost: int = 1,
        rps: float = 5.0,
        burst: int = 20,
    ) -> tuple[bool, float, float]:
        rkey = rate_limit_key(key_id)
        now = time.time()
        try:
            res = await self._client.eval(
                LUA_TOKEN_BUCKET,
                1,
                rkey,
                cost,
                rps,
                burst,
                now,
            )
            allowed = bool(res[0] == 1)
            remaining = float(res[1])
            retry_after = float(res[2])
            return allowed, remaining, retry_after
        except Exception as exc:
            logger.warning("redis_rate_limit_error", key_id=key_id, error=str(exc))
            if self._fail_open:
                # Fail-open: allow request with zero remaining tokens
                return True, 0.0, 0.0
            return False, 0.0, 1.0
