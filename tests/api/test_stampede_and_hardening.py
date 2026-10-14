"""Integration tests for cold-start stampede protection and gateway hardening."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from cachegate.main import create_app
from cachegate.providers.mock import MockProvider


@pytest_asyncio.fixture
async def gateway_client() -> AsyncClient:
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": "Bearer test-key"},
        ) as client:
            yield client


@pytest.mark.asyncio
async def test_cold_cache_stampede_single_provider_call(gateway_client: AsyncClient) -> None:
    """When 50 identical requests arrive on a cold cache, provider is called once."""
    provider_calls = 0
    orig_complete = MockProvider.complete

    async def counting_complete(self: MockProvider, req: object) -> object:
        nonlocal provider_calls
        provider_calls += 1
        await asyncio.sleep(0.05)  # Simulate non-instantaneous provider latency
        return await orig_complete(self, req)  # type: ignore[arg-type]

    payload = {
        "model": "mock-fast",
        "messages": [
            {"role": "system", "content": "You are a stampede test bot."},
            {"role": "user", "content": "What is the capital of Japan?"},
        ],
        "temperature": 0.0,
    }

    with patch.object(MockProvider, "complete", counting_complete):
        # Fire 50 concurrent requests simultaneously
        tasks = [gateway_client.post("/v1/chat/completions", json=payload) for _ in range(50)]
        responses = await asyncio.gather(*tasks)

    # All 50 succeeded
    for resp in responses:
        assert resp.status_code == 200
        data = resp.json()
        assert "choices" in data
        assert len(data["choices"]) > 0

    # Stampede coalescing ensured MockProvider was called only once!
    assert provider_calls == 1

    # Verify at least one HIT-EXACT header from the coalesced requests
    cache_headers = [r.headers.get("X-Cache") for r in responses]
    assert "HIT-EXACT" in cache_headers


@pytest.mark.asyncio
async def test_fail_open_on_cache_failure(gateway_client: AsyncClient) -> None:
    """If cache throws an exception, the gateway fails open and returns 200."""
    payload = {
        "model": "mock-fast",
        "messages": [
            {"role": "user", "content": "Fail open test prompt"},
        ],
        "temperature": 0.0,
    }

    # Patch cache_manager.get to simulate Redis crashing
    with patch(
        "cachegate.cache.manager.CacheManager.get",
        side_effect=RuntimeError("Redis connection timeout"),
    ):
        resp = await gateway_client.post("/v1/chat/completions", json=payload)
        assert resp.status_code == 200
        assert resp.headers.get("X-Cache") == "MISS"
        data = resp.json()
        assert len(data["choices"]) > 0


@pytest.mark.asyncio
async def test_payload_size_limit_rejection(gateway_client: AsyncClient) -> None:
    """Requests exceeding max_request_size_bytes must be rejected with HTTP 413."""
    oversized_headers = {
        "Authorization": "Bearer test-key",
        "Content-Length": "20000000",  # 20MB exceeds default 10MB limit
    }
    resp = await gateway_client.post(
        "/v1/chat/completions",
        content=b"{}",
        headers=oversized_headers,
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"


@pytest.mark.asyncio
async def test_request_bounds_validation(gateway_client: AsyncClient) -> None:
    """Requests violating bounds (e.g. max_tokens > 128000) are rejected."""
    bad_payload = {
        "model": "mock-fast",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 500000,  # Exceeds max 128000
    }
    resp = await gateway_client.post("/v1/chat/completions", json=bad_payload)
    assert resp.status_code in (400, 422)
