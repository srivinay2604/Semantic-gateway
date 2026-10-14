"""Unit tests for fallback chains and circuit breaker integration."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from cachegate.providers.base import ProviderError, ProviderUnavailableError
from cachegate.providers.circuit_breaker import CircuitState
from cachegate.providers.mock import MockProvider
from cachegate.providers.router import ProviderRouter
from cachegate.schemas.internal import (
    InternalMessage,
    InternalRequest,
    InternalResponse,
    InternalUsage,
)


def _make_request(prompt: str = "Fallback test") -> InternalRequest:
    return InternalRequest(
        request_id="test-req-fallback-1",
        model_alias="gpt-4o-mini",
        messages=[InternalMessage(role="user", content=prompt)],
    )


class TestFallbackChain:
    def test_resolve_chain_order(self, tmp_path: object) -> None:
        yaml_content = """
models:
  test-model:
    primary: openai/gpt-4o-mini
    fallbacks: [anthropic/claude-3-haiku-20240307, mock/echo]
"""
        yaml_file = tmp_path / "models.yaml"  # type: ignore[operator]
        yaml_file.write_text(yaml_content)

        router = ProviderRouter(str(yaml_file))
        chain = router.resolve_chain("test-model")
        assert len(chain) == 3
        assert chain[0][1] == "openai/gpt-4o-mini"
        assert chain[1][1] == "anthropic/claude-3-haiku-20240307"
        assert chain[2][1] == "mock/echo"

    @pytest.mark.asyncio
    async def test_primary_success_does_not_call_fallback(self) -> None:
        router = ProviderRouter()

        primary_provider = AsyncMock()
        primary_provider.complete = AsyncMock(
            return_value=InternalResponse(
                response_id="prim-1",
                model="openai/gpt-4o-mini",
                content="Primary answer",
                finish_reason="stop",
                usage=InternalUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
            )
        )

        fallback_provider = AsyncMock()

        router._cache["openai/gpt-4o-mini"] = primary_provider
        router._cache["mock/echo"] = fallback_provider

        resp, served_provider = await router.execute_with_resilience(_make_request())
        assert resp.content == "Primary answer"
        assert served_provider == "openai/gpt-4o-mini"
        assert primary_provider.complete.call_count == 1
        assert fallback_provider.complete.call_count == 0

    @pytest.mark.asyncio
    async def test_primary_failure_falls_back_to_secondary(self) -> None:
        router = ProviderRouter()

        # Primary always fails with 502
        failing_primary = AsyncMock()
        failing_primary.complete = AsyncMock(side_effect=ProviderUnavailableError("OpenAI down"))

        # Mock fallback succeeds
        mock_fallback = MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.0)

        router._cache["openai/gpt-4o-mini"] = failing_primary
        router._cache["mock/echo"] = mock_fallback

        resp, served_provider = await router.execute_with_resilience(_make_request())
        assert served_provider == "mock/echo"
        assert "Fallback test" in resp.content

    @pytest.mark.asyncio
    async def test_circuit_breaker_trips_and_skips_primary(self) -> None:
        router = ProviderRouter()
        failing_primary = AsyncMock()
        failing_primary.complete = AsyncMock(side_effect=ProviderUnavailableError("OpenAI 500"))
        mock_fallback = MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.0)

        router._cache["openai/gpt-4o-mini"] = failing_primary
        router._cache["mock/echo"] = mock_fallback

        breaker = router.get_breaker("openai/gpt-4o-mini")
        breaker.failure_threshold = 3
        breaker.recovery_seconds = 10.0

        # Run 3 requests to trip breaker
        for _ in range(3):
            _, served = await router.execute_with_resilience(_make_request())
            assert served == "mock/echo"

        assert breaker.state == CircuitState.OPEN

        # Call count on primary should be 3 * (1 + settings.provider_max_retries)
        initial_calls = failing_primary.complete.call_count

        # 4th request: Circuit is OPEN, so primary must be skipped entirely (0 calls)!
        _, served = await router.execute_with_resilience(_make_request())
        assert served == "mock/echo"
        assert failing_primary.complete.call_count == initial_calls

    @pytest.mark.asyncio
    async def test_circuit_breaker_recovers_after_cooldown(self) -> None:
        router = ProviderRouter()
        primary = AsyncMock()
        primary.complete = AsyncMock(side_effect=ProviderUnavailableError("OpenAI 500"))
        mock_fallback = MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.0)

        router._cache["openai/gpt-4o-mini"] = primary
        router._cache["mock/echo"] = mock_fallback

        breaker = router.get_breaker("openai/gpt-4o-mini")
        breaker.failure_threshold = 2
        breaker.recovery_seconds = 0.05

        # Trip breaker
        for _ in range(2):
            await router.execute_with_resilience(_make_request())
        assert breaker.state == CircuitState.OPEN

        # Wait for cooldown
        await asyncio.sleep(0.06)
        assert breaker.state == CircuitState.HALF_OPEN

        # Primary now recovers!
        primary.complete = AsyncMock(
            return_value=InternalResponse(
                response_id="rec-1",
                model="openai/gpt-4o-mini",
                content="Recovered answer",
                finish_reason="stop",
                usage=InternalUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
            )
        )

        resp, served = await router.execute_with_resilience(_make_request())
        assert served == "openai/gpt-4o-mini"
        assert resp.content == "Recovered answer"
        assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_all_providers_failing_raises_502(self) -> None:
        router = ProviderRouter()
        failing_p1 = AsyncMock()
        failing_p1.complete = AsyncMock(side_effect=ProviderUnavailableError("P1 down"))
        failing_p2 = AsyncMock()
        failing_p2.complete = AsyncMock(side_effect=ProviderUnavailableError("P2 down"))

        router._cache["openai/gpt-4o-mini"] = failing_p1
        router._cache["mock/echo"] = failing_p2

        with pytest.raises(ProviderError) as exc_info:
            await router.execute_with_resilience(_make_request())

        assert exc_info.value.status_code == 502
