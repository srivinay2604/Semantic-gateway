"""Retry logic with exponential backoff + jitter for upstream provider calls.

Uses `tenacity` to retry transient provider failures (HTTP 429 rate limits,
HTTP 5xx server errors, network timeouts, connection drops).
Never retries non-retryable 4xx client errors (e.g. 400 Bad Request, 401 Unauthorized).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from cachegate.providers.base import ProviderError
from cachegate.settings import settings

logger = structlog.get_logger(__name__)

T = TypeVar("T")


def is_retryable_exception(exc: BaseException) -> bool:
    """Predicate determining if an error should trigger a retry attempt."""
    if isinstance(exc, ProviderError):
        return exc.retryable
    if isinstance(exc, (httpx.TimeoutException, httpx.RequestError)):
        return True
    return False


async def execute_with_retry(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int | None = None,
    min_wait: float = 0.05,
    max_wait: float = 1.0,
    **kwargs: Any,
) -> T:
    """Execute an async provider call with exponential backoff and jitter.

    Args:
        func: The async callable to execute.
        *args: Positional arguments for func.
        max_retries: Number of retry attempts (default from settings.provider_max_retries).
        min_wait: Minimum wait seconds before first retry.
        max_wait: Maximum wait ceiling in seconds.
        **kwargs: Keyword arguments for func.

    Returns:
        The result of func.

    Raises:
        ProviderError: The underlying provider error if all retries are exhausted.
    """
    retries = max_retries if max_retries is not None else settings.provider_max_retries
    attempts = 1 + max(0, retries)

    if attempts <= 1:
        return await func(*args, **kwargs)

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=wait_random_exponential(multiplier=0.1, min=min_wait, max=max_wait),
        retry=retry_if_exception(is_retryable_exception),
        reraise=True,
    ):
        with attempt:
            if attempt.retry_state.attempt_number > 1:
                logger.warning(
                    "provider_call_retry_attempt",
                    attempt=attempt.retry_state.attempt_number,
                    max_attempts=attempts,
                )
            return await func(*args, **kwargs)

    # Should never reach here due to reraise=True, but satisfies mypy
    raise ProviderError("All retry attempts failed", status_code=502)  # pragma: no cover
