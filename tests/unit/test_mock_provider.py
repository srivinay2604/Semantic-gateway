"""Unit tests for MockProvider.

Tests cover:
- Determinism: same input always produces same output.
- Configurable latency: provider respects latency and jitter settings.
- Error rate: provider raises ProviderError with expected probability.
- Token counting: non-zero counts returned.
- health_check returns True.
"""

from __future__ import annotations

import time

import pytest

from cachegate.providers.base import ProviderError
from cachegate.providers.mock import MockProvider, _request_seed
from cachegate.schemas.internal import InternalMessage, InternalRequest


def _make_request(content: str = "Hello, world!", model: str = "mock-fast") -> InternalRequest:
    return InternalRequest(
        request_id="test-req-id",
        model_alias=model,
        messages=[InternalMessage(role="user", content=content)],
    )


class TestMockProviderDeterminism:
    """Same input must always produce the same output (deterministic seed)."""

    @pytest.mark.asyncio
    async def test_same_input_same_output(self) -> None:
        provider = MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.0)
        req = _make_request("What is 2+2?")
        r1 = await provider.complete(req)
        r2 = await provider.complete(req)
        assert r1.content == r2.content

    @pytest.mark.asyncio
    async def test_different_input_different_output(self) -> None:
        provider = MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.0)
        r1 = await provider.complete(_make_request("What is 2+2?"))
        r2 = await provider.complete(_make_request("Tell me about Python."))
        assert r1.response_id != r2.response_id
        # Different seeds should produce different canned responses or echoed content
        seed1 = _request_seed(_make_request("What is 2+2?"))
        seed2 = _request_seed(_make_request("Tell me about Python."))
        assert seed1 != seed2

    @pytest.mark.asyncio
    async def test_echo_contains_last_user_message(self) -> None:
        provider = MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.0)
        content = "unique_test_content_xyz_123"
        resp = await provider.complete(_make_request(content))
        # The mock response should echo (part of) the user message
        assert content[:20] in resp.content

    @pytest.mark.asyncio
    async def test_response_fields_populated(self) -> None:
        provider = MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.0)
        resp = await provider.complete(_make_request("Hello"))
        assert resp.response_id.startswith("mock-")
        assert resp.model == "mock/echo"
        assert resp.finish_reason == "stop"
        assert resp.provider == "mock"


class TestMockProviderTokenCounting:
    @pytest.mark.asyncio
    async def test_nonzero_token_counts(self) -> None:
        provider = MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.0)
        resp = await provider.complete(_make_request("Count my tokens please."))
        assert resp.usage.prompt_tokens > 0
        assert resp.usage.completion_tokens > 0
        assert resp.usage.total_tokens == (resp.usage.prompt_tokens + resp.usage.completion_tokens)

    @pytest.mark.asyncio
    async def test_longer_prompt_more_tokens(self) -> None:
        provider = MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.0)
        short = await provider.complete(_make_request("Hi"))
        long = await provider.complete(
            _make_request("This is a much longer message testing token counting.")
        )
        assert long.usage.prompt_tokens > short.usage.prompt_tokens


class TestMockProviderLatency:
    @pytest.mark.asyncio
    async def test_respects_configured_latency(self) -> None:
        provider = MockProvider(latency_ms=50, jitter_ms=0, error_rate=0.0)
        t0 = time.monotonic()
        await provider.complete(_make_request("ping"))
        elapsed_ms = (time.monotonic() - t0) * 1000
        # Should be at least 40 ms (allow 20% under for CI timing variance)
        assert elapsed_ms >= 40

    @pytest.mark.asyncio
    async def test_zero_latency_is_fast(self) -> None:
        provider = MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.0)
        t0 = time.monotonic()
        await provider.complete(_make_request("ping"))
        elapsed_ms = (time.monotonic() - t0) * 1000
        # Should complete in under 500 ms
        assert elapsed_ms < 500

    @pytest.mark.asyncio
    async def test_provider_latency_ms_field_populated(self) -> None:
        provider = MockProvider(latency_ms=50, jitter_ms=0, error_rate=0.0)
        resp = await provider.complete(_make_request("ping"))
        assert resp.provider_latency_ms >= 0


class TestMockProviderErrorRate:
    @pytest.mark.asyncio
    async def test_zero_error_rate_never_errors(self) -> None:
        provider = MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.0)
        for _ in range(10):
            resp = await provider.complete(_make_request("ping"))
            assert resp.content is not None

    @pytest.mark.asyncio
    async def test_full_error_rate_always_errors(self) -> None:
        provider = MockProvider(latency_ms=0, jitter_ms=0, error_rate=1.0)
        with pytest.raises(ProviderError) as exc_info:
            await provider.complete(_make_request("ping"))
        assert exc_info.value.status_code == 500
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_partial_error_rate_sometimes_errors(self) -> None:
        """With 50% error rate, expect some errors and some successes over many calls."""
        provider = MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.5)
        errors = 0
        successes = 0
        for _ in range(30):
            try:
                await provider.complete(_make_request("ping"))
                successes += 1
            except ProviderError:
                errors += 1
        # With 30 trials at 50% rate, probability of all-success or all-failure is negligible
        assert errors > 0, "Expected at least one error"
        assert successes > 0, "Expected at least one success"


class TestMockProviderHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_always_true(self) -> None:
        provider = MockProvider()
        result = await provider.health_check()
        assert result is True
