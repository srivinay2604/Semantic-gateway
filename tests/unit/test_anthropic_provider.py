"""Unit tests for AnthropicProvider adapter using respx."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from cachegate.providers.anthropic import AnthropicProvider
from cachegate.providers.base import (
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from cachegate.schemas.internal import InternalMessage, InternalRequest


def _make_request(prompt: str = "Hello Claude", system: str | None = None) -> InternalRequest:
    messages = []
    if system:
        messages.append(InternalMessage(role="system", content=system))
    messages.append(InternalMessage(role="user", content=prompt))

    return InternalRequest(
        request_id="test-req-anthropic-1",
        model_alias="claude-haiku",
        resolved_provider="anthropic/claude-3-haiku-20240307",
        messages=messages,
        temperature=0.5,
        max_tokens=256,
    )


class TestAnthropicProviderNonStreaming:
    @respx.mock
    @pytest.mark.asyncio
    async def test_complete_success(self) -> None:
        mock_response = {
            "id": "msg_01X9kLmNoPqRsTuVwXyZ",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello from Claude!"}],
            "model": "claude-3-haiku-20240307",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 15, "output_tokens": 18},
        }

        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, json=mock_response)
        )

        provider = AnthropicProvider(
            model="claude-3-haiku-20240307",
            api_key="sk-ant-test",
            base_url="https://api.anthropic.com/v1",
        )
        resp = await provider.complete(_make_request("Tell me a fact", system="Be concise"))

        assert resp.response_id == "msg_01X9kLmNoPqRsTuVwXyZ"
        assert resp.content == "Hello from Claude!"
        assert resp.model == "claude-3-haiku-20240307"
        assert resp.finish_reason == "stop"
        assert resp.usage.prompt_tokens == 15
        assert resp.usage.completion_tokens == 18
        assert resp.usage.total_tokens == 33
        assert resp.provider == "anthropic"

        # Verify system prompt was moved to top-level 'system'
        sent_body = json.loads(route.calls[0].request.content.decode())
        assert sent_body.get("system") == "Be concise"
        assert len(sent_body["messages"]) == 1
        assert sent_body["messages"][0]["role"] == "user"

    @respx.mock
    @pytest.mark.asyncio
    async def test_complete_401_unauthorized(self) -> None:
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(401, text="Invalid x-api-key")
        )

        provider = AnthropicProvider(api_key="bad-key", base_url="https://api.anthropic.com/v1")
        with pytest.raises(ProviderError) as exc_info:
            await provider.complete(_make_request())

        assert exc_info.value.status_code == 401
        assert not exc_info.value.retryable

    @respx.mock
    @pytest.mark.asyncio
    async def test_complete_429_rate_limit(self) -> None:
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(429, text="Rate limit exceeded")
        )

        provider = AnthropicProvider(api_key="test-key", base_url="https://api.anthropic.com/v1")
        with pytest.raises(ProviderRateLimitError) as exc_info:
            await provider.complete(_make_request())

        assert exc_info.value.status_code == 429
        assert exc_info.value.retryable

    @respx.mock
    @pytest.mark.asyncio
    async def test_complete_500_unavailable(self) -> None:
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(529, text="Overloaded")
        )

        provider = AnthropicProvider(api_key="test-key", base_url="https://api.anthropic.com/v1")
        with pytest.raises(ProviderError):
            await provider.complete(_make_request())

    @respx.mock
    @pytest.mark.asyncio
    async def test_complete_timeout(self) -> None:
        respx.post("https://api.anthropic.com/v1/messages").mock(
            side_effect=httpx.ReadTimeout("Timeout connecting")
        )

        provider = AnthropicProvider(api_key="test-key", base_url="https://api.anthropic.com/v1")
        with pytest.raises(ProviderTimeoutError):
            await provider.complete(_make_request())


class TestAnthropicProviderStreaming:
    @respx.mock
    @pytest.mark.asyncio
    async def test_stream_complete_translates_sse_events(self) -> None:
        sse_body = (
            "event: message_start\n"
            'data: {"type":"message_start","message":{"id":"msg_123"}}\n\n'
            "event: content_block_delta\n"
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello "}}\n\n'
            "event: content_block_delta\n"
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"World!"}}\n\n'
            "event: message_delta\n"
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n'
            "event: message_stop\n"
            'data: {"type":"message_stop"}\n\n'
        )

        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=sse_body.encode("utf-8"),
            )
        )

        provider = AnthropicProvider(
            model="claude-3-haiku-20240307",
            api_key="sk-test",
            base_url="https://api.anthropic.com/v1",
        )
        chunks = []
        async for chunk in provider.stream_complete(_make_request()):
            chunks.append(chunk)

        assert len(chunks) == 4  # 2 content chunks + 1 stop chunk + [DONE]
        assert chunks[-1] == "[DONE]"

        # Check content translation
        c1 = json.loads(chunks[0])
        assert c1["choices"][0]["delta"]["content"] == "Hello "

        c2 = json.loads(chunks[1])
        assert c2["choices"][0]["delta"]["content"] == "World!"

        # Check finish reason translation
        c3 = json.loads(chunks[2])
        assert c3["choices"][0]["finish_reason"] == "stop"
