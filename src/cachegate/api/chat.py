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

import time
import uuid
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from cachegate.providers.base import ProviderError
from cachegate.schemas.internal import InternalMessage, InternalRequest
from cachegate.schemas.openai import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ChoiceMessage,
    OpenAIErrorResponse,
    UsageInfo,
)

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

        async def sse_event_generator() -> AsyncIterator[str]:
            try:
                async for chunk_str in stream_gen:
                    yield f"data: {chunk_str}\n\n"
            except Exception as exc:
                logger.exception("unexpected_streaming_midstream_error", error=str(exc))
                yield "data: [DONE]\n\n"

        stream_headers = {
            "X-Request-Id": request_id,
            "X-Cache": "MISS",
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
    try:
        if hasattr(provider_router, "execute_with_resilience"):
            internal_response, served_provider = await provider_router.execute_with_resilience(
                internal_request
            )
        else:
            internal_response = await provider.complete(internal_request)
            served_provider = provider_str
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
        cache_result=internal_response.cache_result.value,
        prompt_tokens=internal_response.usage.prompt_tokens,
        completion_tokens=internal_response.usage.completion_tokens,
        latency_ms=gateway_latency_ms,
        status=200,
    )

    headers = {
        "X-Request-Id": request_id,
        "X-Cache": internal_response.cache_result.value,
        "X-Provider": internal_response.provider,
        "X-Gateway-Latency-Ms": str(gateway_latency_ms),
    }
    if internal_response.cache_similarity is not None:
        headers["X-Cache-Similarity"] = str(internal_response.cache_similarity)

    return JSONResponse(
        content=completion.model_dump(),
        headers=headers,
    )
