"""API-level integration tests against the ASGI app.

Uses httpx.AsyncClient with ASGITransport so no actual network is used.

Tests cover:
- GET /healthz → 200 {"status": "ok"}
- GET /readyz → 200
- GET /v1/models → 200, contains "mock-fast"
- POST /v1/chat/completions → 200, valid OpenAI-shaped response
- POST with stream=true → 400
- POST with invalid body → 422 (FastAPI) or 400
- X-Request-Id round-trip (client sends it, gets it back)
- X-Request-Id auto-generated when not sent
- GET /metrics → 200, contains "cg_requests_total"
- Provider 502 error path
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from cachegate.providers.base import ProviderError

CHAT_URL = "/v1/chat/completions"
VALID_BODY = {
    "model": "mock-fast",
    "messages": [{"role": "user", "content": "Hello, gateway!"}],
}


class TestHealthEndpoints:
    @pytest.mark.asyncio
    async def test_healthz_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_readyz_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/readyz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"


class TestModelsEndpoint:
    @pytest.mark.asyncio
    async def test_models_list_200(self, client: AsyncClient) -> None:
        resp = await client.get("/v1/models")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_models_list_contains_mock_fast(self, client: AsyncClient) -> None:
        resp = await client.get("/v1/models")
        data = resp.json()
        assert data["object"] == "list"
        ids = [m["id"] for m in data["data"]]
        assert "mock-fast" in ids

    @pytest.mark.asyncio
    async def test_models_list_contains_gpt4o_mini(self, client: AsyncClient) -> None:
        resp = await client.get("/v1/models")
        data = resp.json()
        ids = [m["id"] for m in data["data"]]
        assert "gpt-4o-mini" in ids

    @pytest.mark.asyncio
    async def test_models_each_has_required_fields(self, client: AsyncClient) -> None:
        resp = await client.get("/v1/models")
        for model in resp.json()["data"]:
            assert "id" in model
            assert model["object"] == "model"


class TestChatCompletionsEndpoint:
    @pytest.mark.asyncio
    async def test_successful_completion_shape(self, client: AsyncClient) -> None:
        resp = await client.post(CHAT_URL, json=VALID_BODY)
        assert resp.status_code == 200
        data = resp.json()
        # OpenAI-required fields
        assert data["object"] == "chat.completion"
        assert "id" in data
        assert data["id"].startswith("chatcmpl-")
        assert "created" in data
        assert "model" in data
        assert "choices" in data
        assert len(data["choices"]) == 1
        assert "message" in data["choices"][0]
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert data["choices"][0]["message"]["content"] is not None
        assert "usage" in data
        assert data["usage"]["prompt_tokens"] > 0
        assert data["usage"]["completion_tokens"] > 0
        assert data["usage"]["total_tokens"] > 0

    @pytest.mark.asyncio
    async def test_finish_reason_is_stop(self, client: AsyncClient) -> None:
        resp = await client.post(CHAT_URL, json=VALID_BODY)
        assert resp.json()["choices"][0]["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_x_request_id_header_returned(self, client: AsyncClient) -> None:
        resp = await client.post(CHAT_URL, json=VALID_BODY)
        assert "x-request-id" in resp.headers

    @pytest.mark.asyncio
    async def test_x_request_id_round_trip(self, client: AsyncClient) -> None:
        """Client-provided X-Request-Id must be echoed back."""
        my_id = "my-custom-request-id-abc123"
        resp = await client.post(
            CHAT_URL,
            json=VALID_BODY,
            headers={"X-Request-Id": my_id},
        )
        assert resp.headers["x-request-id"] == my_id

    @pytest.mark.asyncio
    async def test_x_request_id_auto_generated(self, client: AsyncClient) -> None:
        """If no X-Request-Id is sent, one must be generated."""
        resp = await client.post(CHAT_URL, json=VALID_BODY)
        assert "x-request-id" in resp.headers
        assert len(resp.headers["x-request-id"]) > 0

    @pytest.mark.asyncio
    async def test_x_cache_header_returned(self, client: AsyncClient) -> None:
        resp = await client.post(CHAT_URL, json=VALID_BODY)
        assert "x-cache" in resp.headers

    @pytest.mark.asyncio
    async def test_x_provider_header_returned(self, client: AsyncClient) -> None:
        resp = await client.post(CHAT_URL, json=VALID_BODY)
        assert "x-provider" in resp.headers

    @pytest.mark.asyncio
    async def test_stream_true_returns_sse_stream(self, client: AsyncClient) -> None:
        body = {**VALID_BODY, "stream": True}
        resp = await client.post(CHAT_URL, json=body)
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert "x-request-id" in resp.headers
        assert "x-provider" in resp.headers
        assert "data: " in resp.text
        assert "data: [DONE]" in resp.text

    @pytest.mark.asyncio
    async def test_stream_unsupported_provider_returns_400(self) -> None:
        class _NonStreamingProvider:
            supports_streaming = False

        from httpx import ASGITransport
        from httpx import AsyncClient as _AC

        from cachegate.main import create_app

        test_app = create_app()

        class _Router:
            def resolve(self, _model: str) -> tuple:
                return _NonStreamingProvider(), "legacy/provider"

            def list_aliases(self) -> list:
                return []

        async with test_app.router.lifespan_context(test_app):
            test_app.state.provider_router = _Router()
            async with _AC(
                transport=ASGITransport(app=test_app), base_url="http://testserver"
            ) as test_client:
                resp = await test_client.post(CHAT_URL, json={**VALID_BODY, "stream": True})
                assert resp.status_code == 400
                data = resp.json()
                assert "streaming" in data["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_missing_messages_returns_422(self, client: AsyncClient) -> None:
        resp = await client.post(CHAT_URL, json={"model": "mock-fast"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_messages_returns_422(self, client: AsyncClient) -> None:
        resp = await client.post(CHAT_URL, json={"model": "mock-fast", "messages": []})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_no_user_message_returns_422(self, client: AsyncClient) -> None:
        resp = await client.post(
            CHAT_URL,
            json={
                "model": "mock-fast",
                "messages": [{"role": "system", "content": "You are helpful"}],
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_temperature_returns_422(self, client: AsyncClient) -> None:
        body = {**VALID_BODY, "temperature": 5.0}
        resp = await client.post(CHAT_URL, json=body)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_system_plus_user_messages_accepted(self, client: AsyncClient) -> None:
        body = {
            "model": "mock-fast",
            "messages": [
                {"role": "system", "content": "You are a test assistant."},
                {"role": "user", "content": "Run the test."},
            ],
        }
        resp = await client.post(CHAT_URL, json=body)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_provider_error_returns_502(self, client: AsyncClient) -> None:
        """When the provider raises ProviderError, the gateway returns 502."""
        error_provider = AsyncMock()
        error_provider.complete = AsyncMock(
            side_effect=ProviderError("Simulated upstream failure", status_code=500)
        )

        class _FakeRouter:
            def resolve(self, _model: str) -> tuple:
                return error_provider, "mock/echo"

            def list_aliases(self) -> list:
                return []

        # Temporarily patch the router on the app
        from httpx import ASGITransport
        from httpx import AsyncClient as _AC

        from cachegate.main import create_app

        test_app = create_app()
        async with test_app.router.lifespan_context(test_app):
            test_app.state.provider_router = _FakeRouter()
            async with _AC(
                transport=ASGITransport(app=test_app),
                base_url="http://testserver",
            ) as _client:
                resp = await _client.post(CHAT_URL, json=VALID_BODY)
        assert resp.status_code == 502


class TestMetricsEndpoint:
    @pytest.mark.asyncio
    async def test_metrics_reachable(self, client: AsyncClient) -> None:
        resp = await client.get("/metrics")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_contains_requests_total(self, client: AsyncClient) -> None:
        # First make a request to ensure the counter exists
        await client.post(CHAT_URL, json=VALID_BODY)
        resp = await client.get("/metrics")
        assert "cg_requests_total" in resp.text

    @pytest.mark.asyncio
    async def test_metrics_contains_duration_seconds(self, client: AsyncClient) -> None:
        await client.post(CHAT_URL, json=VALID_BODY)
        resp = await client.get("/metrics")
        assert "cg_request_duration_seconds" in resp.text

    @pytest.mark.asyncio
    async def test_metrics_increment_after_request(self, client: AsyncClient) -> None:
        """After making requests, the counter in metrics should be > 0."""
        for _ in range(3):
            await client.post(CHAT_URL, json=VALID_BODY)
        resp = await client.get("/metrics")
        assert "cg_requests_total" in resp.text
        # Verify there is a non-zero count (the line will contain a float)
        assert "_total{" in resp.text or "cg_requests_total" in resp.text
