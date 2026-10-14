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

        from openai import AsyncOpenAI

        sdk = AsyncOpenAI(
            base_url="http://testserver/v1", api_key="test-key", http_client=raw_client
        )
        resp = await sdk.chat.completions.create(
            model="mock-fast",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "What is the capital of France?"},
            ],
            temperature=0.0,
            max_tokens=100,
        )

        assert resp.id.startswith("chatcmpl-")
        assert resp.model == "mock-fast"
        assert len(resp.choices) >= 1
        assert resp.choices[0].message.role == "assistant"
        assert resp.choices[0].message.content is not None
        assert len(resp.choices[0].message.content) > 0
        assert resp.choices[0].finish_reason == "stop"
        assert resp.usage is not None
        assert resp.usage.prompt_tokens > 0

    @pytest.mark.asyncio
    async def test_sdk_streaming_completion(self, sdk_app_client: tuple) -> None:
        """The openai SDK must stream chunks from the gateway with stream=True."""
        raw_client, application = sdk_app_client

        from openai import AsyncOpenAI

        sdk = AsyncOpenAI(
            base_url="http://testserver/v1", api_key="test-key", http_client=raw_client
        )
        stream = await sdk.chat.completions.create(
            model="mock-fast",
            messages=[{"role": "user", "content": "Stream this test sentence"}],
            stream=True,
        )

        collected_words: list[str] = []
        finish_reasons: list[str] = []
        async for chunk in stream:
            assert chunk.id.startswith("chatcmpl-")
            if chunk.choices and chunk.choices[0].delta.content:
                collected_words.append(chunk.choices[0].delta.content)
            if chunk.choices and chunk.choices[0].finish_reason:
                finish_reasons.append(chunk.choices[0].finish_reason)

        assert len(collected_words) > 0
        full_text = "".join(collected_words)
        assert len(full_text) > 0
        assert "stop" in finish_reasons

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
                "model": "mock-fast",
                "messages": [{"role": "user", "content": "Hello from SDK test"}],
                "stream": False,
            },
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["object"] == "chat.completion"

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
