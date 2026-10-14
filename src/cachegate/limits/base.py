"""Base interface for rate limiting."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseRateLimiter(ABC):
    """Abstract interface for rate limiting."""

    @abstractmethod
    async def check(
        self,
        key_id: str,
        cost: int = 1,
        rps: float = 5.0,
        burst: int = 20,
    ) -> tuple[bool, float, float]:
        """Check if request is permitted.

        Returns:
            Tuple of (allowed: bool, remaining_tokens: float, retry_after_seconds: float).
        """
        ...
