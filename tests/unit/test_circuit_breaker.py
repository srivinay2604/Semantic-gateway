"""Unit tests for CircuitBreaker state machine."""

from __future__ import annotations

import asyncio

import pytest

from cachegate.providers.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreakerStates:
    @pytest.mark.asyncio
    async def test_initial_state_is_closed(self) -> None:
        cb = CircuitBreaker("test-provider", failure_threshold=3, recovery_seconds=10.0)
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_failures == 0
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_success_resets_failures(self) -> None:
        cb = CircuitBreaker("test-provider", failure_threshold=3, recovery_seconds=10.0)
        await cb.record_failure()
        await cb.record_failure()
        assert cb.consecutive_failures == 2
        assert cb.state == CircuitState.CLOSED

        await cb.record_success()
        assert cb.consecutive_failures == 0
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_trips_to_open_after_threshold(self) -> None:
        cb = CircuitBreaker("test-provider", failure_threshold=3, recovery_seconds=10.0)
        for _ in range(3):
            await cb.record_failure()

        assert cb.state == CircuitState.OPEN
        assert cb.consecutive_failures == 3
        assert await cb.allow_request() is False
        assert cb.remaining_recovery_seconds() > 0

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_timeout(self) -> None:
        # Very short recovery duration for fast test
        cb = CircuitBreaker("test-provider", failure_threshold=2, recovery_seconds=0.05)
        await cb.record_failure()
        await cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.06)
        assert cb.state == CircuitState.HALF_OPEN
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_half_open_success_closes_circuit(self) -> None:
        cb = CircuitBreaker("test-provider", failure_threshold=2, recovery_seconds=0.05)
        await cb.record_failure()
        await cb.record_failure()
        await asyncio.sleep(0.06)
        assert cb.state == CircuitState.HALF_OPEN

        await cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens_circuit(self) -> None:
        cb = CircuitBreaker("test-provider", failure_threshold=2, recovery_seconds=0.05)
        await cb.record_failure()
        await cb.record_failure()
        await asyncio.sleep(0.06)
        assert cb.state == CircuitState.HALF_OPEN

        await cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert await cb.allow_request() is False

    @pytest.mark.asyncio
    async def test_concurrent_failures_safety(self) -> None:
        cb = CircuitBreaker("test-provider", failure_threshold=10, recovery_seconds=1.0)
        await asyncio.gather(*[cb.record_failure() for _ in range(10)])
        assert cb.consecutive_failures == 10
        assert cb.state == CircuitState.OPEN
