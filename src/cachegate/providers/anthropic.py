"""Anthropic provider adapter.

Translates InternalRequest to the Anthropic Messages API format (/v1/messages),
handles both non-streaming and SSE streaming responses (translating Anthropic
SSE events to OpenAI-compatible chunks), and maps errors to ProviderError subclasses.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx
import structlog

from cachegate.providers.base import (
    Provider,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from cachegate.schemas.internal import InternalRequest, InternalResponse, InternalUsage
from cachegate.schemas.openai import (
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionChunkDelta,
)
from cachegate.settings import settings

logger = structlog.get_logger(__name__)


class AnthropicProvider(Provider):
    """Adapter for official Anthropic Messages API.

    Supports non-streaming and SSE streaming with translation to OpenAI chunk shape.
    """

    supports_streaming: bool = True

    def __init__(
        self,
        model: str = "claude-3-haiku-20240307",
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key if api_key is not None else settings.anthropic_api_key
        self._base_url = (base_url if base_url is not None else settings.anthropic_base_url).rstrip(
            "/"
        )
        self._timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.provider_timeout_seconds
        )
        self._client = client

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(timeout=self._timeout_seconds)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    def _build_payload(self, request: InternalRequest, stream: bool = False) -> dict[str, Any]:
        system_prompts: list[str] = []
        messages: list[dict[str, Any]] = []

        for m in request.messages:
            if m.role == "system":
                if m.content:
                    system_prompts.append(m.content)
            else:
                role = "assistant" if m.role == "assistant" else "user"
                messages.append({"role": role, "content": m.content or ""})

        if not messages:
            messages.append({"role": "user", "content": ""})

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": request.max_tokens if request.max_tokens is not None else 1024,
            "stream": stream,
        }
        if system_prompts:
            payload["system"] = "\n\n".join(system_prompts)
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop is not None:
            if isinstance(request.stop, str):
                payload["stop_sequences"] = [request.stop]
            else:
                payload["stop_sequences"] = list(request.stop)

        return payload

    def _map_error_status(self, status_code: int, body_text: str) -> ProviderError:
        msg = f"Anthropic error (HTTP {status_code}): {body_text}"
        if status_code in (401, 403):
            return ProviderError(msg, status_code=status_code, retryable=False)
        if status_code == 429:
            return ProviderRateLimitError(msg)
        if status_code in (500, 502, 503, 504):
            return ProviderUnavailableError(msg)
        return ProviderError(msg, status_code=status_code, retryable=False)

    def _map_stop_reason(
        self, stop_reason: str | None
    ) -> Literal["stop", "length", "content_filter", "tool_calls"]:
        if stop_reason == "end_turn":
            return "stop"
        if stop_reason == "max_tokens":
            return "length"
        return "stop"

    async def complete(self, request: InternalRequest) -> InternalResponse:
        """Call Anthropic non-streaming messages API."""
        payload = self._build_payload(request, stream=False)
        url = f"{self._base_url}/messages"
        headers = self._headers()
        t0 = time.monotonic()

        client = self._get_client()
        should_close = client is not self._client

        try:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.is_error:
                raise self._map_error_status(resp.status_code, resp.text)
            data: dict[str, Any] = resp.json()
        except httpx.TimeoutException as exc:
            logger.warning("anthropic_timeout", error=str(exc), model=self._model)
            raise ProviderTimeoutError(f"Anthropic call timed out: {exc}") from exc
        except httpx.RequestError as exc:
            logger.error("anthropic_network_error", error=str(exc), model=self._model)
            raise ProviderUnavailableError(f"Anthropic network error: {exc}") from exc
        finally:
            if should_close:
                await client.aclose()

        latency_ms = (time.monotonic() - t0) * 1000

        # Extract content text blocks
        content_parts = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                content_parts.append(block.get("text", ""))
        reply_text = "".join(content_parts)

        usage_data = data.get("usage", {})
        prompt_tokens = usage_data.get("input_tokens", 0)
        completion_tokens = usage_data.get("output_tokens", 0)
        total_tokens = prompt_tokens + completion_tokens

        return InternalResponse(
            response_id=data.get("id", f"msg-anthropic-{int(t0)}"),
            model=data.get("model", f"anthropic/{self._model}"),
            content=reply_text,
            finish_reason=self._map_stop_reason(data.get("stop_reason")),
            usage=InternalUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            ),
            provider_latency_ms=latency_ms,
            provider="anthropic",
        )

    async def stream_complete(self, request: InternalRequest) -> AsyncIterator[str]:
        """Stream chunks from Anthropic, translating SSE events to OpenAI chunk JSON."""
        payload = self._build_payload(request, stream=True)
        url = f"{self._base_url}/messages"
        headers = self._headers()

        client = self._get_client()
        should_close = client is not self._client

        chunk_id = f"chatcmpl-anthropic-{uuid.uuid4().hex}"
        model_name = f"anthropic/{self._model}"

        try:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.is_error:
                    error_bytes = await resp.aread()
                    raise self._map_error_status(resp.status_code, error_bytes.decode())

                current_event: str | None = None

                async for line in resp.aiter_lines():
                    trimmed = line.strip()
                    if not trimmed:
                        continue
                    if trimmed.startswith("event: "):
                        current_event = trimmed[7:].strip()
                        continue
                    if trimmed.startswith("data: "):
                        raw_data = trimmed[6:].strip()
                        if not raw_data:
                            continue
                        try:
                            event_data = json.loads(raw_data)
                        except json.JSONDecodeError:
                            continue

                        event_type = event_data.get("type", current_event)

                        if event_type == "content_block_delta":
                            delta = event_data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                chunk = ChatCompletionChunk(
                                    id=chunk_id,
                                    model=model_name,
                                    choices=[
                                        ChatCompletionChunkChoice(
                                            index=0,
                                            delta=ChatCompletionChunkDelta(content=text),
                                            finish_reason=None,
                                        )
                                    ],
                                )
                                yield chunk.model_dump_json()

                        elif event_type == "message_delta":
                            stop_reason = event_data.get("delta", {}).get("stop_reason")
                            mapped_finish = self._map_stop_reason(stop_reason)
                            final_chunk = ChatCompletionChunk(
                                id=chunk_id,
                                model=model_name,
                                choices=[
                                    ChatCompletionChunkChoice(
                                        index=0,
                                        delta=ChatCompletionChunkDelta(),
                                        finish_reason=mapped_finish,
                                    )
                                ],
                            )
                            yield final_chunk.model_dump_json()

                        elif event_type == "message_stop":
                            yield "[DONE]"

        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"Anthropic stream timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(f"Anthropic stream network error: {exc}") from exc
        finally:
            if should_close:
                await client.aclose()

    async def health_check(self) -> bool:
        """Verify provider availability."""
        return True
