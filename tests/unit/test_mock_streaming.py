"""Unit tests for MockProvider streaming functionality."""

from __future__ import annotations

import json

import pytest

from cachegate.providers.base import ProviderError
from cachegate.providers.mock import MockProvider
from cachegate.schemas.internal import InternalMessage, InternalRequest


def _make_request(prompt: str) -> InternalRequest:
    return InternalRequest(
        request_id="test-req-stream-1",
        model_alias="mock-fast",
        resolved_provider="mock/echo",
        messages=[InternalMessage(role="user", content=prompt)],
    )


class TestMockProviderStreaming:
    @pytest.mark.asyncio
    async def test_stream_chunks_structure_and_done(self) -> None:
        provider = MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.0)
        chunks = []
        async for chunk in provider.stream_complete(_make_request("Hello stream!")):
            chunks.append(chunk)

        assert len(chunks) >= 3  # role chunk + at least 1 word chunk + stop chunk + [DONE]
        assert chunks[-1] == "[DONE]"

        # First chunk should have assistant role
        first_parsed = json.loads(chunks[0])
        assert first_parsed["object"] == "chat.completion.chunk"
        assert first_parsed["choices"][0]["delta"]["role"] == "assistant"

        # Final non-DONE chunk should have finish_reason='stop'
        last_json = json.loads(chunks[-2])
        assert last_json["choices"][0]["finish_reason"] == "stop"

        # All intermediate chunks must be valid JSON
        for c in chunks[:-1]:
            parsed = json.loads(c)
            assert "id" in parsed
            assert "choices" in parsed

    @pytest.mark.asyncio
    async def test_stream_simulated_error(self) -> None:
        provider = MockProvider(latency_ms=0, jitter_ms=0, error_rate=1.0)
        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider.stream_complete(_make_request("Hello")):
                pass
        assert exc_info.value.status_code == 500
