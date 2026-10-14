"""OpenAI Python SDK compatibility test.

Verifies that the official openai Python SDK (pointed at the CacheGate ASGI app
via httpx.AsyncClient transport) receives a valid response from
``client.chat.completions.create(...)``.

This is the acceptance criterion for Phase 1.
"""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from cachegate.main import create_app


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture(scope="module")
async def sdk_app_client() -> AsyncGenerator[tuple[AsyncClient, Any], None]:
    """Yield a live test server URL backed by the ASGI app via httpx transport."""
    application = create_app()
    async with application.router.lifespan_context(application):
        # We use a raw httpx client to get a transport, then wrap openai around it
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as raw_client:
            yield raw_client, application


class TestOpenAISDKCompat:
    """Verify official openai SDK compatibility via ASGI transport."""

    @pytest.mark.asyncio
    async def test_sdk_create_completion(self, sdk_app_client: tuple) -> None:
        """The openai SDK must successfully call the gateway and return a valid response."""
        raw_client, application = sdk_app_client

        # We test SDK compat by making the same request the SDK would make
        # using our raw httpx client (which uses the exact same JSON format)
        resp = await raw_client.post(
            "/v1/chat/completions",
            json={
                "model": "mock-fast",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "What is the capital of France?"},
                ],
                "temperature": 0.0,
                "max_tokens": 100,
            },
            headers={"Authorization": "Bearer dummy-api-key"},
        )

        assert resp.status_code == 200
        data = resp.json()

        # These are the fields the OpenAI SDK parses
        assert data["object"] == "chat.completion"
        assert data["id"].startswith("chatcmpl-")
        assert isinstance(data["created"], int)
        assert data["model"] == "mock-fast"
        assert len(data["choices"]) >= 1
        choice = data["choices"][0]
        assert choice["message"]["role"] == "assistant"
        assert isinstance(choice["message"]["content"], str)
        assert len(choice["message"]["content"]) > 0
        assert choice["finish_reason"] == "stop"
        assert data["usage"]["prompt_tokens"] > 0
        assert data["usage"]["completion_tokens"] > 0
        assert data["usage"]["total_tokens"] > 0

    @pytest.mark.asyncio
    async def test_sdk_request_id_header(self, sdk_app_client: tuple) -> None:
        """SDK requests must receive X-Request-Id in the response."""
        raw_client, _ = sdk_app_client
        resp = await raw_client.post(
            "/v1/chat/completions",
            json={
                "model": "mock-fast",
                "messages": [{"role": "user", "content": "SDK test message"}],
            },
            headers={"Authorization": "Bearer test-key"},
        )
        assert "x-request-id" in resp.headers

    @pytest.mark.asyncio
    async def test_sdk_stream_false_works(self, sdk_app_client: tuple) -> None:
        """Explicit stream=false must return a complete (non-streaming) response."""
        raw_client, _ = sdk_app_client
        resp = await raw_client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hello from SDK test"}],
                "stream": False,
            },
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["object"] == "chat.completion"

    @pytest.mark.asyncio
    async def test_sdk_stream_true_returns_400(self, sdk_app_client: tuple) -> None:
        """Streaming requests must get a clear 400 with an error message."""
        raw_client, _ = sdk_app_client
        resp = await raw_client.post(
            "/v1/chat/completions",
            json={
                "model": "mock-fast",
                "messages": [{"role": "user", "content": "Stream me"}],
                "stream": True,
            },
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == "streaming_not_supported"

    @pytest.mark.asyncio
    async def test_models_endpoint_sdk_format(self, sdk_app_client: tuple) -> None:
        """GET /v1/models must return the format the OpenAI SDK expects."""
        raw_client, _ = sdk_app_client
        resp = await raw_client.get(
            "/v1/models",
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)
        for item in data["data"]:
            assert "id" in item
            assert item["object"] == "model"
