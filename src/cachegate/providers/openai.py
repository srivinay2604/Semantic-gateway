"""OpenAI provider adapter.

Translates InternalRequest to the OpenAI Chat Completions API format, handles
both non-streaming and SSE streaming responses, and maps errors to ProviderError
subclasses.
"""

from __future__ import annotations

import time
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
from cachegate.settings import settings

logger = structlog.get_logger(__name__)


class OpenAIProvider(Provider):
    """Adapter for official OpenAI Chat Completions API.

    Supports both buffered (``complete``) and SSE (``stream_complete``) calls.
    """

    supports_streaming: bool = True

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key if api_key is not None else settings.openai_api_key
        self._base_url = (base_url if base_url is not None else settings.openai_base_url).rstrip(
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
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _build_payload(self, request: InternalRequest, stream: bool = False) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for m in request.messages:
            msg_dict: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.name:
                msg_dict["name"] = m.name
            messages.append(msg_dict)

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": stream,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop is not None:
            payload["stop"] = request.stop
        if request.user:
            payload["user"] = request.user
        return payload

    def _map_error_status(self, status_code: int, body_text: str) -> ProviderError:
        msg = f"OpenAI error (HTTP {status_code}): {body_text}"
        if status_code in (401, 403):
            return ProviderError(msg, status_code=status_code, retryable=False)
        if status_code == 429:
            return ProviderRateLimitError(msg)
        if status_code in (500, 502, 503, 504):
            return ProviderUnavailableError(msg)
        return ProviderError(msg, status_code=status_code, retryable=False)

    def _map_finish_reason(
        self, reason: str | None
    ) -> Literal["stop", "length", "content_filter", "tool_calls"]:
        if reason in ("stop", "length", "content_filter", "tool_calls"):
            return reason  # type: ignore[return-value]
        return "stop"

    async def complete(self, request: InternalRequest) -> InternalResponse:
        """Call OpenAI non-streaming chat completions."""
        payload = self._build_payload(request, stream=False)
        url = f"{self._base_url}/chat/completions"
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
            logger.warning("openai_timeout", error=str(exc), model=self._model)
            raise ProviderTimeoutError(f"OpenAI call timed out: {exc}") from exc
        except httpx.RequestError as exc:
            logger.error("openai_network_error", error=str(exc), model=self._model)
            raise ProviderUnavailableError(f"OpenAI network error: {exc}") from exc
        finally:
            if should_close:
                await client.aclose()

        latency_ms = (time.monotonic() - t0) * 1000
        choices = data.get("choices", [{}])
        choice = choices[0] if choices else {}
        message = choice.get("message", {})
        usage_data = data.get("usage", {})

        prompt_tokens = usage_data.get("prompt_tokens", 0)
        completion_tokens = usage_data.get("completion_tokens", 0)
        total_tokens = usage_data.get("total_tokens", prompt_tokens + completion_tokens)

        return InternalResponse(
            response_id=data.get("id", f"chatcmpl-openai-{int(t0)}"),
            model=data.get("model", f"openai/{self._model}"),
            content=message.get("content", ""),
            finish_reason=self._map_finish_reason(choice.get("finish_reason")),
            usage=InternalUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            ),
            provider_latency_ms=latency_ms,
            provider="openai",
        )

    async def stream_complete(self, request: InternalRequest) -> AsyncIterator[str]:
        """Stream chunks from OpenAI as raw SSE json lines, ending with '[DONE]'."""
        payload = self._build_payload(request, stream=True)
        url = f"{self._base_url}/chat/completions"
        headers = self._headers()

        client = self._get_client()
        should_close = client is not self._client

        try:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.is_error:
                    error_bytes = await resp.aread()
                    raise self._map_error_status(resp.status_code, error_bytes.decode())

                async for line in resp.aiter_lines():
                    trimmed = line.strip()
                    if not trimmed:
                        continue
                    if trimmed.startswith("data: "):
                        data_str = trimmed[6:].strip()
                        if data_str == "[DONE]":
                            yield "[DONE]"
                        elif data_str:
                            yield data_str
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"OpenAI stream timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(f"OpenAI stream network error: {exc}") from exc
        finally:
            if should_close:
                await client.aclose()

    async def health_check(self) -> bool:
        """Verify provider availability."""
        if not self._api_key:
            return True
        client = self._get_client()
        should_close = client is not self._client
        try:
            resp = await client.get(f"{self._base_url}/models", headers=self._headers())
            return resp.status_code == 200
        except Exception:
            return False
        finally:
            if should_close:
                await client.aclose()
