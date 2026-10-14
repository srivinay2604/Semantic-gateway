"""Circuit breaker implementation for CacheGate providers.

Implements the standard Circuit Breaker pattern with three states:
- CLOSED: Requests pass through; failures are counted.
- OPEN: Upstream dependency is down; calls fail-fast with CircuitBreakerOpenError.
- HALF_OPEN: Cooldown has elapsed; exactly one request is allowed to probe recovery.

States transition automatically based on consecutive failure threshold and
cooldown recovery seconds.
"""

from __future__ import annotations

import asyncio
import time
from enum import StrEnum

import structlog

from cachegate.observability.metrics import circuit_breaker_state
from cachegate.providers.base import ProviderError
from cachegate.settings import settings

logger = structlog.get_logger(__name__)


class CircuitState(StrEnum):
    """The three states of a circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(ProviderError):
    """Raised when an attempt is made to call a provider whose circuit is OPEN."""

    def __init__(self, provider: str, recovery_seconds_remaining: float) -> None:
        super().__init__(
            f"Circuit breaker for provider '{provider}' is OPEN. "
            f"Recovery probe in {recovery_seconds_remaining:.1f}s.",
            status_code=503,
            retryable=False,
        )
        self.provider = provider
        self.recovery_seconds_remaining = recovery_seconds_remaining


class CircuitBreaker:
    """In-process circuit breaker protecting against cascading provider outages.

    Args:
        provider_name: Identifier for the provider (e.g. 'openai/gpt-4o-mini').
        failure_threshold: Consecutive failures before opening the circuit.
        recovery_seconds: Duration in seconds to stay OPEN before moving to HALF_OPEN.
    """

    def __init__(
        self,
        provider_name: str,
        failure_threshold: int | None = None,
        recovery_seconds: float | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.failure_threshold = (
            failure_threshold
            if failure_threshold is not None
            else settings.breaker_failure_threshold
        )
        self.recovery_seconds = (
            recovery_seconds
            if recovery_seconds is not None
            else float(settings.breaker_recovery_seconds)
        )

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def state(self) -> CircuitState:
        """Current circuit state, automatically evaluating cooldown expiry."""
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.recovery_seconds:
                return CircuitState.HALF_OPEN
        return self._state

    def remaining_recovery_seconds(self) -> float:
        """Seconds remaining before an OPEN circuit moves to HALF_OPEN."""
        if self._state != CircuitState.OPEN or self._opened_at is None:
            return 0.0
        elapsed = time.monotonic() - self._opened_at
        return max(0.0, self.recovery_seconds - elapsed)

    async def allow_request(self) -> bool:
        """Check whether a request should be allowed through the circuit."""
        async with self._lock:
            current = self.state
            if current == CircuitState.HALF_OPEN and self._state == CircuitState.OPEN:
                self._transition_to(CircuitState.HALF_OPEN)
                return True
            return current in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    async def record_success(self) -> None:
        """Record a successful provider call, resetting failures and closing circuit."""
        async with self._lock:
            if self._state != CircuitState.CLOSED:
                logger.info(
                    "circuit_breaker_recovered",
                    provider=self.provider_name,
                    previous_state=self._state.value,
                )
            self._consecutive_failures = 0
            self._opened_at = None
            self._transition_to(CircuitState.CLOSED)

    async def record_failure(self) -> None:
        """Record a provider failure, tripping the circuit if threshold is reached."""
        async with self._lock:
            self._consecutive_failures += 1
            current = self.state

            should_trip = (
                current == CircuitState.HALF_OPEN
                or self._consecutive_failures >= self.failure_threshold
            )
            if should_trip:
                self._opened_at = time.monotonic()
                self._transition_to(CircuitState.OPEN)
                logger.warning(
                    "circuit_breaker_tripped",
                    provider=self.provider_name,
                    failures=self._consecutive_failures,
                    threshold=self.failure_threshold,
                    recovery_seconds=self.recovery_seconds,
                )

    def _transition_to(self, new_state: CircuitState) -> None:
        if self._state != new_state:
            logger.debug(
                "circuit_breaker_state_change",
                provider=self.provider_name,
                from_state=self._state.value,
                to_state=new_state.value,
            )
            self._state = new_state
            try:
                circuit_breaker_state.labels(
                    provider=self.provider_name, state=new_state.value
                ).inc()
            except Exception:
                pass
