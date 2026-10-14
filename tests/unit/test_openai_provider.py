"""Unit tests for OpenAIProvider adapter using respx."""

from __future__ import annotations

import httpx
import pytest
import respx

from cachegate.providers.base import (
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from cachegate.providers.openai import OpenAIProvider
from cachegate.schemas.internal import InternalMessage, InternalRequest


def _make_request(prompt: str = "Hello OpenAI") -> InternalRequest:
    return InternalRequest(
        request_id="test-req-openai-1",
        model_alias="gpt-4o-mini",
        resolved_provider="openai/gpt-4o-mini",
        messages=[InternalMessage(role="user", content=prompt)],
        temperature=0.7,
        max_tokens=100,
    )


class TestOpenAIProviderNonStreaming:
    @respx.mock
    @pytest.mark.asyncio
    async def test_complete_success(self) -> None:
        mock_response = {
            "id": "chatcmpl-test-123",
            "object": "chat.completion",
            "created": 1677652288,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello from mock OpenAI!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 9, "completion_tokens": 12, "total_tokens": 21},
        }

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=mock_response)
        )

        provider = OpenAIProvider(
            model="gpt-4o-mini",
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
        )
        resp = await provider.complete(_make_request())

        assert resp.response_id == "chatcmpl-test-123"
        assert resp.content == "Hello from mock OpenAI!"
        assert resp.model == "gpt-4o-mini"
        assert resp.finish_reason == "stop"
        assert resp.usage.prompt_tokens == 9
        assert resp.usage.completion_tokens == 12
        assert resp.usage.total_tokens == 21
        assert resp.provider == "openai"

    @respx.mock
    @pytest.mark.asyncio
    async def test_complete_401_unauthorized(self) -> None:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(401, text="Incorrect API key")
        )

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-bad")
        with pytest.raises(ProviderError) as exc_info:
            await provider.complete(_make_request())

        assert exc_info.value.status_code == 401
        assert not exc_info.value.retryable

    @respx.mock
    @pytest.mark.asyncio
    async def test_complete_429_rate_limit(self) -> None:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(429, text="Rate limit exceeded")
        )

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        with pytest.raises(ProviderRateLimitError) as exc_info:
            await provider.complete(_make_request())

        assert exc_info.value.status_code == 429
        assert exc_info.value.retryable

    @respx.mock
    @pytest.mark.asyncio
    async def test_complete_500_unavailable(self) -> None:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        with pytest.raises(ProviderUnavailableError) as exc_info:
            await provider.complete(_make_request())

        assert exc_info.value.status_code == 502 or exc_info.value.status_code == 500
        assert exc_info.value.retryable

    @respx.mock
    @pytest.mark.asyncio
    async def test_complete_timeout(self) -> None:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            side_effect=httpx.ReadTimeout("Timeout connecting")
        )

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        with pytest.raises(ProviderTimeoutError) as exc_info:
            await provider.complete(_make_request())

        assert exc_info.value.status_code == 504
        assert exc_info.value.retryable


class TestOpenAIProviderStreaming:
    @respx.mock
    @pytest.mark.asyncio
    async def test_stream_complete_success(self) -> None:
        sse_body = (
            'data: {"id":"chatcmpl-stream-1","choices":[{"delta":{"content":"Hi"}}]}\n\n'
            'data: {"id":"chatcmpl-stream-1","choices":[{"delta":{"content":" there"}}]}\n\n'
            "data: [DONE]\n\n"
        )

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=sse_body.encode("utf-8"),
            )
        )

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        chunks = []
        async for chunk in provider.stream_complete(_make_request()):
            chunks.append(chunk)

        assert len(chunks) == 3
        assert '{"content":"Hi"}' in chunks[0]
        assert '{"content":" there"}' in chunks[1]
        assert chunks[2] == "[DONE]"

    @respx.mock
    @pytest.mark.asyncio
    async def test_stream_error_status(self) -> None:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(429, text="Rate limit hit")
        )

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        with pytest.raises(ProviderRateLimitError):
            async for _ in provider.stream_complete(_make_request()):
                pass


class TestOpenAIProviderHealthCheck:
    @respx.mock
    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        respx.get("https://api.openai.com/v1/models").mock(return_value=httpx.Response(200))
        provider = OpenAIProvider(api_key="sk-test")
        assert await provider.health_check() is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_check_failure(self) -> None:
        respx.get("https://api.openai.com/v1/models").mock(return_value=httpx.Response(500))
        provider = OpenAIProvider(api_key="sk-test")
        assert await provider.health_check() is False
