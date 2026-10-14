"""OpenAI-compatible request and response Pydantic v2 models.

Implements the subset of the OpenAI chat completions API required for Phase 1.
The models are intentionally strict so that invalid requests are rejected at the
validation layer rather than being forwarded to providers.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """A single message in the conversation."""

    role: Literal["system", "user", "assistant", "tool", "function"]
    content: str | None = None
    name: str | None = None

    @field_validator("content")
    @classmethod
    def content_not_empty_for_user(cls, v: str | None) -> str | None:
        """Allow None content only for assistant messages (tool use pattern)."""
        return v


class UsageInfo(BaseModel):
    """Token usage counts returned in every response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChoiceMessage(BaseModel):
    """The message object inside a choice."""

    role: Literal["assistant"] = "assistant"
    content: str | None = None


class Choice(BaseModel):
    """A single completion choice."""

    index: int = 0
    message: ChoiceMessage
    finish_reason: Literal["stop", "length", "content_filter", "tool_calls"] | None = "stop"


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request body.

    Reference: https://platform.openai.com/docs/api-reference/chat/create
    Only fields supported by the gateway are included here. Unknown fields
    are ignored via ``extra="ignore"``.
    """

    model_config = ConfigDict(extra="ignore")

    model: str = Field(..., min_length=1, description="Model alias or ID")
    messages: list[ChatMessage] = Field(..., min_length=1)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=1)
    stop: str | list[str] | None = None
    stream: bool | None = Field(default=False)
    user: str | None = None

    @model_validator(mode="after")
    def messages_must_have_user(self) -> ChatCompletionRequest:
        """Ensure at least one user message is present."""
        roles = [m.role for m in self.messages]
        if "user" not in roles:
            raise ValueError("messages must contain at least one message with role='user'")
        return self

    @model_validator(mode="after")
    def user_messages_must_have_content(self) -> ChatCompletionRequest:
        """User messages must have non-empty content."""
        for m in self.messages:
            if m.role == "user" and not m.content:
                raise ValueError("user messages must have non-empty content")
        return self


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response."""

    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex}")
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[Choice]
    usage: UsageInfo

    # Optional gateway-specific fields (not part of OpenAI spec, ignored by SDK)
    system_fingerprint: str | None = None


# ---------------------------------------------------------------------------
# Error model
# ---------------------------------------------------------------------------


class OpenAIError(BaseModel):
    """Inner error object matching OpenAI error format."""

    message: str
    type: str
    code: str | None = None
    param: str | None = None


class OpenAIErrorResponse(BaseModel):
    """Top-level error response matching OpenAI error format.

    Usage::
        raise HTTPException(
            status_code=400,
            detail=OpenAIErrorResponse.bad_request("msg").model_dump(),
        )
    """

    error: OpenAIError

    @classmethod
    def make(
        cls,
        message: str,
        error_type: str,
        code: str | None = None,
        param: str | None = None,
    ) -> OpenAIErrorResponse:
        return cls(error=OpenAIError(message=message, type=error_type, code=code, param=param))

    @classmethod
    def bad_request(cls, message: str) -> OpenAIErrorResponse:
        return cls.make(message=message, error_type="invalid_request_error", code="invalid_request")

    @classmethod
    def streaming_not_supported(cls) -> OpenAIErrorResponse:
        return cls.make(
            message="Streaming is not yet supported. Set stream=false or omit the field.",
            error_type="invalid_request_error",
            code="streaming_not_supported",
        )

    @classmethod
    def model_not_found(cls, model: str) -> OpenAIErrorResponse:
        return cls.make(
            message=f"The model '{model}' does not exist.",
            error_type="invalid_request_error",
            code="model_not_found",
        )


# ---------------------------------------------------------------------------
# Models list response
# ---------------------------------------------------------------------------


class ModelObject(BaseModel):
    """Single model entry in the /v1/models list."""

    id: str
    object: Literal["model"] = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "cachegate"


class ModelsListResponse(BaseModel):
    """Response for GET /v1/models."""

    object: Literal["list"] = "list"
    data: list[ModelObject]

    # Keep extra fields out of __dict__ for clean SDK compat
    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# Streaming chunk models (Phase 2)
# ---------------------------------------------------------------------------


class ChatCompletionChunkDelta(BaseModel):
    """Delta object within a streaming chunk choice."""

    role: str | None = None
    content: str | None = None


class ChatCompletionChunkChoice(BaseModel):
    """A single choice within a streaming chunk."""

    index: int = 0
    delta: ChatCompletionChunkDelta
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"] | None = None
    logprobs: Any = None


class ChatCompletionChunk(BaseModel):
    """OpenAI-compatible streaming chunk."""

    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[ChatCompletionChunkChoice]
    system_fingerprint: str | None = None
    usage: UsageInfo | None = None

    model_config = ConfigDict(extra="ignore")
