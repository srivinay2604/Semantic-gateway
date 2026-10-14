"""API integration tests for Phase 3 Resilience (fallbacks and circuit breakers)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from cachegate.main import create_app
from cachegate.providers.base import ProviderUnavailableError
from cachegate.providers.mock import MockProvider
from cachegate.providers.router import ProviderRouter

CHAT_URL = "/v1/chat/completions"
VALID_BODY = {
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Integration resilience test"}],
}


class TestResilienceAPI:
    @pytest.mark.asyncio
    async def test_api_returns_fallback_provider_header_on_primary_failure(self) -> None:
        """When primary fails, the gateway returns 200 with X-Provider set to the fallback."""
        app = create_app()

        failing_primary = AsyncMock()
        failing_primary.complete = AsyncMock(
            side_effect=ProviderUnavailableError("Primary upstream outage")
        )
        mock_fallback = MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.0)

        async with app.router.lifespan_context(app):
            router: ProviderRouter = app.state.provider_router
            router._cache["openai/gpt-4o-mini"] = failing_primary
            router._cache["mock/echo"] = mock_fallback

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                resp = await client.post(CHAT_URL, json=VALID_BODY)
                assert resp.status_code == 200
                assert resp.headers["x-provider"] == "mock/echo"
                data = resp.json()
                assert len(data["choices"]) > 0

    @pytest.mark.asyncio
    async def test_api_returns_502_when_all_fallbacks_fail(self) -> None:
        """When all providers in the fallback chain fail, return 502 Bad Gateway."""
        app = create_app()

        p1 = AsyncMock()
        p1.complete = AsyncMock(side_effect=ProviderUnavailableError("P1 500"))
        p2 = AsyncMock()
        p2.complete = AsyncMock(side_effect=ProviderUnavailableError("P2 500"))

        async with app.router.lifespan_context(app):
            router: ProviderRouter = app.state.provider_router
            router._cache["openai/gpt-4o-mini"] = p1
            router._cache["mock/echo"] = p2

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                resp = await client.post(CHAT_URL, json=VALID_BODY)
                assert resp.status_code == 502
                data = resp.json()
                assert "error" in data
                assert data["error"]["code"] == "provider_error"

    @pytest.mark.asyncio
    async def test_api_streaming_fallback(self) -> None:
        """If primary fails to start stream, falls back to secondary and streams successfully."""
        app = create_app()

        failing_primary = AsyncMock()
        failing_primary.supports_streaming = True

        async def _failing_stream(*_args: object, **_kwargs: object) -> AsyncIterator[str]:
            raise ProviderUnavailableError("Streaming start failure")
            yield ""

        failing_primary.stream_complete = _failing_stream
        mock_fallback = MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.0)

        async with app.router.lifespan_context(app):
            router: ProviderRouter = app.state.provider_router
            router._cache["openai/gpt-4o-mini"] = failing_primary
            router._cache["mock/echo"] = mock_fallback

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                resp = await client.post(CHAT_URL, json={**VALID_BODY, "stream": True})
                assert resp.status_code == 200
                assert resp.headers["x-provider"] == "mock/echo"
                assert "text/event-stream" in resp.headers["content-type"]
                assert "data: [DONE]" in resp.text
