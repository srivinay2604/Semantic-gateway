"""Unit tests for token bucket rate limiter with fake clock and concurrency."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from cachegate.limits.token_bucket import MemoryTokenBucketRateLimiter, RedisTokenBucketRateLimiter


class FakeClock:
    def __init__(self, initial_time: float = 1000.0) -> None:
        self.current_time = initial_time

    def __call__(self) -> float:
        return self.current_time

    def advance(self, seconds: float) -> None:
        self.current_time += seconds


class TestMemoryTokenBucketRateLimiter:
    @pytest.mark.asyncio
    async def test_burst_capacity_and_exhaustion(self) -> None:
        clock = FakeClock()
        limiter = MemoryTokenBucketRateLimiter(clock_fn=clock)

        # Allow burst of 3 requests
        for _ in range(3):
            allowed, remaining, retry_after = await limiter.check("k1", cost=1, rps=1.0, burst=3)
            assert allowed is True
            assert retry_after == 0.0

        # 4th request must be denied
        allowed, remaining, retry_after = await limiter.check("k1", cost=1, rps=1.0, burst=3)
        assert allowed is False
        assert retry_after > 0.9

    @pytest.mark.asyncio
    async def test_refill_over_time(self) -> None:
        clock = FakeClock()
        limiter = MemoryTokenBucketRateLimiter(clock_fn=clock)

        # Consume 2 tokens out of 2
        await limiter.check("k2", cost=2, rps=2.0, burst=2)
        allowed, _, _ = await limiter.check("k2", cost=1, rps=2.0, burst=2)
        assert allowed is False

        # Advance 1.0 second -> refills 2 tokens
        clock.advance(1.0)
        allowed, remaining, _ = await limiter.check("k2", cost=1, rps=2.0, burst=2)
        assert allowed is True

    @pytest.mark.asyncio
    async def test_concurrency_race_condition(self) -> None:
        """Concurrent requests respect total capacity."""
        limiter = MemoryTokenBucketRateLimiter()

        async def _attempt() -> bool:
            allowed, _, _ = await limiter.check("concurrent_key", cost=1, rps=1.0, burst=5)
            return allowed

        results = await asyncio.gather(*[_attempt() for _ in range(10)])
        # Exactly 5 should be allowed and 5 denied
        assert results.count(True) == 5
        assert results.count(False) == 5


class TestRedisTokenBucketRateLimiter:
    @pytest.mark.asyncio
    async def test_redis_limiter_mocked(self) -> None:
        mock_client = AsyncMock()
        # Lua script returns {1, "4.0000", "0"}
        mock_client.eval = AsyncMock(return_value=[1, "4.0000", "0"])

        limiter = RedisTokenBucketRateLimiter(redis_client=mock_client)
        allowed, remaining, retry_after = await limiter.check("r_key", cost=1, rps=5.0, burst=10)
        assert allowed is True
        assert remaining == 4.0
        assert retry_after == 0.0

    @pytest.mark.asyncio
    async def test_redis_limiter_fail_open(self) -> None:
        mock_client = AsyncMock()
        mock_client.eval = AsyncMock(side_effect=Exception("Redis down"))

        limiter = RedisTokenBucketRateLimiter(redis_client=mock_client, fail_open=True)
        allowed, remaining, _ = await limiter.check("r_key")
        assert allowed is True
