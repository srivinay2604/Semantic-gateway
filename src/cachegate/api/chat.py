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

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

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
) -> JSONResponse:
    """OpenAI-compatible chat completions endpoint.

    Validates the request, resolves the model, calls the provider, and
    returns a response with gateway-specific headers.
    """
    t0 = time.monotonic()

    # Reject streaming in Phase 1
    if body.stream:
        error = OpenAIErrorResponse.streaming_not_supported()
        return JSONResponse(status_code=400, content=error.model_dump())

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

    # Call provider
    try:
        internal_response = await provider.complete(internal_request)
    except ProviderError as exc:
        logger.error(
            "provider_error",
            provider=provider_str,
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
        provider=provider_str,
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
