"""API acceptance tests for Phase 4 Exact Cache."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from cachegate.cache.manager import CacheManager
from cachegate.cache.stores.memory_store import MemoryExactStore
from cachegate.main import create_app
from cachegate.schemas.internal import InternalResponse, TokenUsage

CHAT_URL = "/v1/chat/completions"
BODY = {
    "model": "mock-fast",
    "messages": [{"role": "user", "content": "What is 2 + 2?"}],
    "temperature": 0.0,
}


class TestExactCacheAPI:
    @pytest.mark.asyncio
    async def test_exact_cache_hit_skips_provider_and_latency(self) -> None:
        """First request is MISS and calls provider.

        Second identical request returns HIT-EXACT, fast latency, and provider call count remains 1.
        """
        app = create_app()
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(
            return_value=InternalResponse(
                response_id="test-123",
                model="mock-fast",
                content="2 + 2 is 4.",
                finish_reason="stop",
                provider="mock/echo",
                usage=TokenUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
            )
        )
        mock_provider.supports_streaming = True

        async with app.router.lifespan_context(app):
            # Use isolated memory store for test stability
            memory_store = MemoryExactStore()
            app.state.cache_manager = CacheManager(exact_store=memory_store)
            app.state.provider_router._cache["mock/echo"] = mock_provider

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                # 1. First request -> MISS
                resp1 = await client.post(CHAT_URL, json=BODY)
                assert resp1.status_code == 200
                assert resp1.headers["x-cache"] == "MISS"
                assert mock_provider.complete.call_count == 1

                # 2. Second identical request -> HIT-EXACT
                resp2 = await client.post(CHAT_URL, json=BODY)
                assert resp2.status_code == 200
                assert resp2.headers["x-cache"] == "HIT-EXACT"
                assert resp2.headers["x-provider"] == "cached"
                assert mock_provider.complete.call_count == 1  # Provider was skipped!
                data2 = resp2.json()
                assert data2["choices"][0]["message"]["content"] == "2 + 2 is 4."

                latency_str = resp2.headers.get("x-gateway-latency-ms", "999")
                assert float(latency_str) < 50.0  # Fast local hit

    @pytest.mark.asyncio
    async def test_cache_bypass_header_forces_provider_call(self) -> None:
        """Client sending X-Cache-Bypass: true gets X-Cache: BYPASS and invokes provider."""
        app = create_app()
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(
            return_value=InternalResponse(
                response_id="test-123",
                model="mock-fast",
                content="2 + 2 is 4.",
                finish_reason="stop",
                provider="mock/echo",
                usage=TokenUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
            )
        )
        mock_provider.supports_streaming = True

        async with app.router.lifespan_context(app):
            memory_store = MemoryExactStore()
            app.state.cache_manager = CacheManager(exact_store=memory_store)
            app.state.provider_router._cache["mock/echo"] = mock_provider

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                headers = {"x-cache-bypass": "true"}
                resp = await client.post(CHAT_URL, json=BODY, headers=headers)
                assert resp.status_code == 200
                assert resp.headers["x-cache"] == "BYPASS"
                assert mock_provider.complete.call_count == 1

    @pytest.mark.asyncio
    async def test_exact_cache_streaming_hit_replay(self) -> None:
        """Streaming identical request after miss replays chunks from cache."""
        app = create_app()
        async with app.router.lifespan_context(app):
            memory_store = MemoryExactStore()
            app.state.cache_manager = CacheManager(exact_store=memory_store)

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                # 1. Non-streaming call populates cache
                await client.post(CHAT_URL, json=BODY)

                # 2. Streaming call for identical prompt hits cache and streams chunks
                resp = await client.post(CHAT_URL, json={**BODY, "stream": True})
                assert resp.status_code == 200
                assert resp.headers["x-cache"] == "HIT-EXACT"
                assert "text/event-stream" in resp.headers["content-type"]
                assert "data: [DONE]" in resp.text
