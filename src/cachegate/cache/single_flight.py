"""Single-flight request coalescing for CacheGate.

Protects against cache stampede: when multiple identical requests arrive
concurrently on a cold cache, only one request executes the upstream provider call.
The others wait for the in-flight result and return the shared response.

Combines:
1. In-process asyncio coalescing via shared Futures.
2. Cross-worker / cross-process distributed locking via Redis (`cg:v1:lock:{exact_hash}`).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import structlog

from cachegate.observability.metrics import singleflight_coalesced_total

logger = structlog.get_logger(__name__)

T = TypeVar("T")


class SingleFlight:
    """Coalesces concurrent requests for identical cache keys."""

    def __init__(self, redis_client: Any | None = None, lock_ttl_ms: int = 10000) -> None:
        self._redis = redis_client
        self._lock_ttl_ms = lock_ttl_ms
        self._in_flight: dict[str, asyncio.Future[Any]] = {}
        self._lock = asyncio.Lock()

    async def execute(
        self,
        key: str,
        fn: Callable[[], Awaitable[T]],
        cache_lookup_fn: Callable[[], Awaitable[T | None]] | None = None,
    ) -> tuple[T, bool]:
        """Execute `fn` for `key` ensuring only one call runs concurrently.

        Returns:
            A tuple of `(result, coalesced: bool)`.
            coalesced is True if this invocation hitched a ride on another in-flight call,
            or False if this was the leader executing `fn`.
        """
        async with self._lock:
            if key in self._in_flight:
                fut = self._in_flight[key]
                singleflight_coalesced_total.inc()
                logger.info("singleflight_coalescing_in_process", key=key)
                is_waiter = True
            else:
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                self._in_flight[key] = fut
                is_waiter = False

        if is_waiter:
            # Wait for the in-flight leader to finish
            result = await asyncio.shield(fut)
            return result, True

        # We are the in-process leader.
        # Now handle cross-process distributed coordination via Redis (if available).
        lock_token = str(uuid.uuid4())
        redis_lock_key = f"cg:v1:lock:{key}"
        have_redis_lock = False

        try:
            if self._redis is not None:
                try:
                    acquired = await self._redis.set(
                        redis_lock_key,
                        lock_token,
                        nx=True,
                        px=self._lock_ttl_ms,
                    )
                    if not acquired:
                        # Another process holds the distributed lock.
                        logger.info("singleflight_waiting_distributed_lock", key=key)
                        singleflight_coalesced_total.inc()
                        poll_start = time.monotonic()
                        max_wait_seconds = min(5.0, self._lock_ttl_ms / 1000.0)

                        while (time.monotonic() - poll_start) < max_wait_seconds:
                            await asyncio.sleep(0.05)
                            if cache_lookup_fn is not None:
                                cached_result = await cache_lookup_fn()
                                if cached_result is not None:
                                    fut.set_result(cached_result)
                                    return cached_result, True

                            # Check if lock was released
                            lock_val = await self._redis.get(redis_lock_key)
                            if lock_val is None:
                                if cache_lookup_fn is not None:
                                    cached_result = await cache_lookup_fn()
                                    if cached_result is not None:
                                        fut.set_result(cached_result)
                                        return cached_result, True
                                break

                        logger.warning(
                            "singleflight_distributed_lock_timeout_fallback",
                            key=key,
                        )
                    else:
                        have_redis_lock = True
                except Exception as exc:
                    logger.warning(
                        "singleflight_redis_lock_error_failopen",
                        error=str(exc),
                    )

            # Execute the underlying function
            result = await fn()
            fut.set_result(result)
            return result, False

        except Exception as exc:
            fut.set_exception(exc)
            raise

        finally:
            async with self._lock:
                self._in_flight.pop(key, None)

            if have_redis_lock and self._redis is not None:
                try:
                    lua_release = """
                    if redis.call("get", KEYS[1]) == ARGV[1] then
                        return redis.call("del", KEYS[1])
                    else
                        return 0
                    end
                    """
                    await self._redis.eval(lua_release, 1, redis_lock_key, lock_token)
                except Exception as exc:
                    logger.warning("singleflight_lock_release_failed", error=str(exc))
