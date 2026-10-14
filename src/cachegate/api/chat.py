"""POST /v1/chat/completions — the core gateway endpoint.

Phase 1 behaviour:
- Validates the request using Pydantic v2 (ChatCompletionRequest).
- Rejects streaming requests with 400.
- Resolves the model alias via ProviderRouter.
- Calls the resolved provider (MockProvider in Phase 1).
- Returns an OpenAI-compatible response with X-Request-Id, X-Cache, and
  X-Provider headers.

Phase 2 adds: streaming, real providers.
Phase 4 adds: cache lookup before provider call.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from cachegate.cache.keys import compute_exact_request_hash
from cachegate.cache.single_flight import SingleFlight
from cachegate.providers.base import ProviderError
from cachegate.schemas.internal import InternalMessage, InternalRequest, InternalResponse
from cachegate.schemas.openai import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ChoiceMessage,
    OpenAIErrorResponse,
    UsageInfo,
)
from cachegate.settings import settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post(
    "/chat/completions",
    response_model=ChatCompletionResponse,
    summary="Create a chat completion",
    responses={
        400: {"description": "Invalid request"},
        502: {"description": "Upstream provider error"},
    },
)
async def chat_completions(
    request: Request,
    body: ChatCompletionRequest,
    background_tasks: BackgroundTasks,
) -> Response:
    """OpenAI-compatible chat completions endpoint.

    Validates the request, resolves the model, calls the provider, and
    returns a response with gateway-specific headers (or an SSE stream).
    """
    t0 = time.monotonic()

    # Retrieve request_id set by RequestIdMiddleware
    request_id: str = getattr(request.state, "request_id", str(uuid.uuid4()))

    # Bind context for structured logging
    structlog.contextvars.bind_contextvars(
        model=body.model,
        request_id=request_id,
    )

    # Resolve provider
    provider_router = request.app.state.provider_router
    provider, provider_str = provider_router.resolve(body.model)

    logger.info(
        "chat_completion_start",
        model_alias=body.model,
        provider=provider_str,
        num_messages=len(body.messages),
        stream=body.stream,
    )

    # Build internal request
    internal_request = InternalRequest(
        request_id=request_id,
        model_alias=body.model,
        resolved_provider=provider_str,
        messages=[
            InternalMessage(role=m.role, content=m.content, name=m.name) for m in body.messages
        ],
        temperature=body.temperature,
        top_p=body.top_p,
        max_tokens=body.max_tokens,
        stop=body.stop,
        user=body.user,
    )

    # Validate streaming support upfront
    if body.stream and not provider.supports_streaming:
        error = OpenAIErrorResponse.streaming_not_supported()
        return JSONResponse(status_code=400, content=error.model_dump())

    # -------------------------------------------------------------------------
    # Caching Layer Lookup (Phase 4 & 5)
    # -------------------------------------------------------------------------
    cache_manager = getattr(request.app.state, "cache_manager", None)
    usage_tracker = getattr(request.app.state, "usage_tracker", None)
    tenant_id = getattr(request.state, "tenant_id", "default")
    key_id = getattr(request.state, "key_id", "default")

    cached_response = None
    cache_header = "MISS"

    if cache_manager is not None:
        try:
            cached_response, cache_header = await cache_manager.get(
                internal_request,
                headers=request.headers,
                tenant=tenant_id,
            )
        except Exception as exc:
            logger.warning("cache_lookup_failed_failopen", error=str(exc))
            cached_response = None
            cache_header = "MISS"

    # Parse custom TTL header if provided
    custom_ttl: int | None = None
    ttl_header = request.headers.get("x-cache-ttl")
    if ttl_header:
        try:
            custom_ttl = int(ttl_header)
        except ValueError:
            pass

    # Handle cache hit
    if cached_response is not None:
        gateway_latency_ms = round((time.monotonic() - t0) * 1000, 1)
        headers = {
            "X-Request-Id": request_id,
            "X-Cache": cache_header,
            "X-Provider": "cached",
            "X-Gateway-Latency-Ms": str(gateway_latency_ms),
        }
        if cached_response.cache_similarity is not None:
            headers["X-Cache-Similarity"] = str(cached_response.cache_similarity)

        if usage_tracker is not None:
            background_tasks.add_task(
                usage_tracker.record_request,
                key_id=key_id,
                model=body.model,
                prompt_tokens=cached_response.usage.prompt_tokens,
                completion_tokens=cached_response.usage.completion_tokens,
                is_hit=True,
            )

        logger.info(
            "chat_completion_cache_hit",
            model_alias=body.model,
            cache_result=cache_header,
            similarity=cached_response.cache_similarity,
            latency_ms=gateway_latency_ms,
        )

        if body.stream:
            # Replay stream as SSE chunks
            async def cached_sse_event_generator() -> AsyncIterator[str]:
                words = cached_response.content.split(" ")
                for i, w in enumerate(words):
                    piece = w if i == len(words) - 1 else w + " "
                    chunk_obj = {
                        "id": f"chatcmpl-{uuid.uuid4().hex}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": body.model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": piece},
                                "finish_reason": None if i < len(words) - 1 else "stop",
                            }
                        ],
                    }
                    yield f"data: {json.dumps(chunk_obj)}\n\n"
                yield "data: [DONE]\n\n"

            stream_headers = {
                **headers,
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
            return StreamingResponse(
                cached_sse_event_generator(),
                media_type="text/event-stream",
                headers=stream_headers,
            )

        completion = ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex}",
            model=body.model,
            choices=[
                Choice(
                    index=0,
                    message=ChoiceMessage(content=cached_response.content),
                    finish_reason=cached_response.finish_reason,
                )
            ],
            usage=UsageInfo(
                prompt_tokens=cached_response.usage.prompt_tokens,
                completion_tokens=cached_response.usage.completion_tokens,
                total_tokens=cached_response.usage.total_tokens,
            ),
        )
        return JSONResponse(content=completion.model_dump(), headers=headers)

    # -------------------------------------------------------------------------
    # Upstream Provider Execution on Cache Miss / Bypass
    # -------------------------------------------------------------------------
    # Handle streaming
    if body.stream:
        if not provider.supports_streaming:
            error = OpenAIErrorResponse.streaming_not_supported()
            return JSONResponse(status_code=400, content=error.model_dump())

        try:
            if hasattr(provider_router, "stream_with_resilience"):
                stream_gen, served_provider = await provider_router.stream_with_resilience(
                    internal_request
                )
            else:
                stream_gen = provider.stream_complete(internal_request)
                served_provider = provider_str
        except ProviderError as exc:
            logger.error(
                "streaming_fallback_exhausted",
                model=body.model,
                status_code=exc.status_code,
                error=str(exc),
            )
            err = OpenAIErrorResponse.make(
                message=str(exc),
                error_type="upstream_error",
                code="provider_error",
            )
            return JSONResponse(status_code=502, content=err.model_dump())

        accumulated_tokens: list[str] = []
        finish_reason: list[str] = ["stop"]

        async def sse_event_generator() -> AsyncIterator[str]:
            try:
                async for chunk_str in stream_gen:
                    yield f"data: {chunk_str}\n\n"
                    if chunk_str != "[DONE]":
                        try:
                            parsed = json.loads(chunk_str)
                            choices = parsed.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                if "content" in delta and delta["content"]:
                                    accumulated_tokens.append(delta["content"])
                                if choices[0].get("finish_reason"):
                                    finish_reason[0] = choices[0]["finish_reason"]
                        except Exception:
                            pass
            except Exception as exc:
                logger.exception("unexpected_streaming_midstream_error", error=str(exc))
                yield "data: [DONE]\n\n"

            # Post-stream accumulation cache write (spec §9.6)
            full_text = "".join(accumulated_tokens)
            if (
                cache_manager is not None
                and full_text
                and finish_reason[0] == "stop"
                and cache_header != "BYPASS"
            ):
                from cachegate.schemas.internal import CacheResult, InternalUsage

                assembled_resp = InternalResponse(
                    response_id=f"stream-{uuid.uuid4().hex}",
                    model=body.model,
                    content=full_text,
                    finish_reason="stop",
                    provider=served_provider,
                    usage=InternalUsage(
                        prompt_tokens=0,
                        completion_tokens=len(accumulated_tokens),
                        total_tokens=len(accumulated_tokens),
                    ),
                    cache_result=CacheResult.MISS,
                )
                try:
                    await cache_manager.put(
                        internal_request,
                        assembled_resp,
                        ttl_seconds=custom_ttl,
                    )
                except Exception as exc:
                    logger.warning("post_stream_cache_write_failed", error=str(exc))

        stream_headers = {
            "X-Request-Id": request_id,
            "X-Cache": cache_header,
            "X-Provider": served_provider,
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        return StreamingResponse(
            sse_event_generator(),
            media_type="text/event-stream",
            headers=stream_headers,
        )

    # Call provider with resilience (retries, circuit breaker, fallback chain)
    # and single-flight coalescing (Phase 7 stampede protection)
    single_flight: SingleFlight | None = getattr(request.app.state, "single_flight", None)

    async def _call_provider_and_cache() -> tuple[InternalResponse, str]:
        if hasattr(provider_router, "execute_with_resilience"):
            resp, s_prov = await provider_router.execute_with_resilience(internal_request)
        else:
            resp = await provider.complete(internal_request)
            s_prov = provider_str

        # Eagerly store into cache so stampede followers find it immediately
        if cache_manager is not None and cache_header != "BYPASS":
            try:
                await cache_manager.put(
                    internal_request,
                    resp,
                    tenant=tenant_id,
                    ttl_seconds=custom_ttl,
                )
            except Exception as exc:
                logger.warning("singleflight_cache_put_failed", error=str(exc))
        return resp, s_prov

    async def _lookup_cache_stampede() -> tuple[InternalResponse, str] | None:
        if cache_manager is None:
            return None
        cached, _tier = await cache_manager.get(
            internal_request, headers=request.headers, tenant=tenant_id
        )
        if cached is not None:
            return cached, cached.provider
        return None

    try:
        if single_flight is not None and cache_header != "BYPASS" and settings.singleflight_enabled:
            req_hash = compute_exact_request_hash(internal_request)
            flight_key = f"{tenant_id}:{body.model}:{req_hash}"
            (internal_response, served_provider), coalesced = await single_flight.execute(
                key=flight_key,
                fn=_call_provider_and_cache,
                cache_lookup_fn=_lookup_cache_stampede,
            )
            if coalesced:
                cache_header = "HIT-EXACT"
        else:
            internal_response, served_provider = await _call_provider_and_cache()
    except ProviderError as exc:
        logger.error(
            "provider_error_fallbacks_exhausted",
            model_alias=body.model,
            status_code=exc.status_code,
            error=str(exc),
        )
        error = OpenAIErrorResponse.make(
            message=str(exc),
            error_type="upstream_error",
            code="provider_error",
        )
        return JSONResponse(
            status_code=502,
            content=error.model_dump(),
        )
    except Exception as exc:
        logger.exception("unexpected_provider_error", provider=provider_str, error=str(exc))
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    # Record usage in background
    if usage_tracker is not None:
        is_hit = cache_header in ("HIT-EXACT", "HIT-SEMANTIC")
        background_tasks.add_task(
            usage_tracker.record_request,
            key_id=key_id,
            model=body.model,
            prompt_tokens=internal_response.usage.prompt_tokens,
            completion_tokens=internal_response.usage.completion_tokens,
            is_hit=is_hit,
        )

    # Build OpenAI-compatible response
    completion = ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex}",
        model=body.model,
        choices=[
            Choice(
                index=0,
                message=ChoiceMessage(content=internal_response.content),
                finish_reason=internal_response.finish_reason,
            )
        ],
        usage=UsageInfo(
            prompt_tokens=internal_response.usage.prompt_tokens,
            completion_tokens=internal_response.usage.completion_tokens,
            total_tokens=internal_response.usage.total_tokens,
        ),
    )

    gateway_latency_ms = round((time.monotonic() - t0) * 1000, 1)

    logger.info(
        "chat_completion_done",
        model_alias=body.model,
        provider=served_provider,
        cache_result=cache_header,
        prompt_tokens=internal_response.usage.prompt_tokens,
        completion_tokens=internal_response.usage.completion_tokens,
        latency_ms=gateway_latency_ms,
        status=200,
    )

    headers = {
        "X-Request-Id": request_id,
        "X-Cache": cache_header,
        "X-Provider": served_provider,
        "X-Gateway-Latency-Ms": str(gateway_latency_ms),
    }

    return JSONResponse(
        content=completion.model_dump(),
        headers=headers,
    )
