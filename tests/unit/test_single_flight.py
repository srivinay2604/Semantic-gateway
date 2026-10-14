"""Unit tests for SingleFlight request coalescing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from cachegate.cache.single_flight import SingleFlight


@pytest.mark.asyncio
async def test_single_flight_coalescing_concurrent_calls() -> None:
    """Concurrent calls for the exact same key must run fn only once."""
    sf = SingleFlight()
    call_count = 0

    async def slow_fetch() -> str:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return "fetched_data"

    # Fire 20 concurrent tasks
    tasks = [asyncio.create_task(sf.execute("test-key", slow_fetch)) for _ in range(20)]
    results = await asyncio.gather(*tasks)

    # All 20 received the same data
    for res, _ in results:
        assert res == "fetched_data"

    # Exactly 1 leader executed fn
    assert call_count == 1

    # Exactly 1 was leader (coalesced=False), 19 were coalesced (coalesced=True)
    leader_count = sum(1 for _, coalesced in results if not coalesced)
    coalesced_count = sum(1 for _, coalesced in results if coalesced)
    assert leader_count == 1
    assert coalesced_count == 19


@pytest.mark.asyncio
async def test_single_flight_exception_propagation() -> None:
    """If the leader raises an exception, all waiters receive the same exception."""
    sf = SingleFlight()

    async def failing_fetch() -> str:
        await asyncio.sleep(0.02)
        raise RuntimeError("upstream_failed")

    tasks = [asyncio.create_task(sf.execute("err-key", failing_fetch)) for _ in range(5)]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert len(results) == 5
    for r in results:
        assert isinstance(r, RuntimeError)
        assert str(r) == "upstream_failed"


@pytest.mark.asyncio
async def test_single_flight_sequential_calls_not_coalesced() -> None:
    """Sequential calls after completion run separately."""
    sf = SingleFlight()
    call_count = 0

    async def fetch() -> int:
        nonlocal call_count
        call_count += 1
        return call_count

    res1, coal1 = await sf.execute("seq-key", fetch)
    res2, coal2 = await sf.execute("seq-key", fetch)

    assert res1 == 1
    assert not coal1
    assert res2 == 2
    assert not coal2
    assert call_count == 2


@pytest.mark.asyncio
async def test_single_flight_with_mock_redis_distributed_lock() -> None:
    """When another process holds the distributed lock, follower polls cache."""
    mock_redis = AsyncMock()
    # First set() returns False (lock held by someone else)
    mock_redis.set = AsyncMock(return_value=False)
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.eval = AsyncMock(return_value=1)

    sf = SingleFlight(redis_client=mock_redis, lock_ttl_ms=500)

    async def fallback_fn() -> str:
        return "fallback_result"

    async def cache_lookup() -> str | None:
        return "cached_by_leader"

    res, coalesced = await sf.execute("dist-key", fallback_fn, cache_lookup_fn=cache_lookup)
    assert res == "cached_by_leader"
    assert coalesced is True
