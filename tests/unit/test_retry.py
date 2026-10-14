"""Unit tests for execute_with_retry."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from cachegate.providers.base import (
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from cachegate.providers.retry import execute_with_retry, is_retryable_exception


class TestRetryablePredicate:
    def test_retryable_provider_errors(self) -> None:
        assert is_retryable_exception(ProviderRateLimitError("429")) is True
        assert is_retryable_exception(ProviderUnavailableError("502")) is True
        assert is_retryable_exception(ProviderTimeoutError("504")) is True
        assert is_retryable_exception(ProviderError("fail", retryable=True)) is True

    def test_non_retryable_provider_errors(self) -> None:
        assert (
            is_retryable_exception(ProviderError("unauthorized", status_code=401, retryable=False))
            is False
        )
        assert is_retryable_exception(ValueError("bad value")) is False

    def test_network_exceptions_retryable(self) -> None:
        assert is_retryable_exception(httpx.ReadTimeout("read timeout")) is True
        assert is_retryable_exception(httpx.ConnectError("connection refused")) is True


class TestExecuteWithRetry:
    @pytest.mark.asyncio
    async def test_succeeds_first_attempt(self) -> None:
        mock_fn = AsyncMock(return_value="ok")
        res = await execute_with_retry(mock_fn, max_retries=2, min_wait=0.001, max_wait=0.002)
        assert res == "ok"
        assert mock_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_transient_failure_then_succeeds(self) -> None:
        mock_fn = AsyncMock(
            side_effect=[
                ProviderRateLimitError("Rate limit hit"),
                "recovered_ok",
            ]
        )
        res = await execute_with_retry(mock_fn, max_retries=2, min_wait=0.001, max_wait=0.002)
        assert res == "recovered_ok"
        assert mock_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_stops_immediately_on_non_retryable_error(self) -> None:
        mock_fn = AsyncMock(side_effect=ProviderError("Bad key", status_code=401, retryable=False))
        with pytest.raises(ProviderError) as exc_info:
            await execute_with_retry(mock_fn, max_retries=3, min_wait=0.001, max_wait=0.002)

        assert exc_info.value.status_code == 401
        assert mock_fn.call_count == 1  # Never retried!

    @pytest.mark.asyncio
    async def test_exhausts_retries_and_raises(self) -> None:
        mock_fn = AsyncMock(side_effect=ProviderUnavailableError("Server error 500"))
        with pytest.raises(ProviderUnavailableError):
            await execute_with_retry(mock_fn, max_retries=2, min_wait=0.001, max_wait=0.002)

        assert mock_fn.call_count == 3  # initial + 2 retries
