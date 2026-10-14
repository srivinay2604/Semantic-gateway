"""Unit tests for OpenAI-compatible request/response schemas.

Tests cover:
- Valid request acceptance
- Invalid request rejection (missing required fields, wrong types, bad values)
- Edge cases: empty messages list, no user message, streaming field
- Response model construction
- OpenAIErrorResponse factory methods
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cachegate.schemas.openai import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ChoiceMessage,
    OpenAIErrorResponse,
    UsageInfo,
)

# ---------------------------------------------------------------------------
# ChatCompletionRequest — valid cases
# ---------------------------------------------------------------------------


class TestChatCompletionRequestValid:
    def test_minimal_valid_request(self) -> None:
        req = ChatCompletionRequest(
            model="mock-fast",
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert req.model == "mock-fast"
        assert len(req.messages) == 1
        assert req.messages[0].role == "user"
        assert req.stream is False

    def test_full_request_with_all_fields(self) -> None:
        req = ChatCompletionRequest(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "What is 2+2?"},
            ],
            temperature=0.0,
            top_p=1.0,
            max_tokens=256,
            stop=["END"],
            stream=False,
            user="user-123",
        )
        assert req.temperature == 0.0
        assert req.top_p == 1.0
        assert req.max_tokens == 256
        assert req.stop == ["END"]
        assert req.user == "user-123"

    def test_extra_fields_are_ignored(self) -> None:
        """Extra fields must be silently ignored (not raise an error)."""
        req = ChatCompletionRequest(
            model="mock-fast",
            messages=[{"role": "user", "content": "hi"}],
            unknown_field_xyz="ignored",  # type: ignore[call-arg]
        )
        assert req.model == "mock-fast"

    def test_stop_as_single_string(self) -> None:
        req = ChatCompletionRequest(
            model="mock-fast",
            messages=[{"role": "user", "content": "hi"}],
            stop="STOP",
        )
        assert req.stop == "STOP"

    def test_stream_defaults_to_false(self) -> None:
        req = ChatCompletionRequest(
            model="mock-fast",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert req.stream is False

    def test_stream_true_accepted(self) -> None:
        """stream=true should be accepted by the schema (rejected at route level)."""
        req = ChatCompletionRequest(
            model="mock-fast",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
        assert req.stream is True

    def test_assistant_message_with_none_content(self) -> None:
        """Assistant messages can have None content (tool-use pattern)."""
        req = ChatCompletionRequest(
            model="mock-fast",
            messages=[
                {"role": "user", "content": "ping"},
                {"role": "assistant", "content": None},
            ],
        )
        assert req.messages[1].content is None


# ---------------------------------------------------------------------------
# ChatCompletionRequest — invalid cases
# ---------------------------------------------------------------------------


class TestChatCompletionRequestInvalid:
    def test_missing_model_raises(self) -> None:
        with pytest.raises(ValidationError):
            ChatCompletionRequest(  # type: ignore[call-arg]
                messages=[{"role": "user", "content": "hi"}]
            )

    def test_empty_model_string_raises(self) -> None:
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="",
                messages=[{"role": "user", "content": "hi"}],
            )

    def test_missing_messages_raises(self) -> None:
        with pytest.raises(ValidationError):
            ChatCompletionRequest(model="mock-fast")  # type: ignore[call-arg]

    def test_empty_messages_list_raises(self) -> None:
        with pytest.raises(ValidationError):
            ChatCompletionRequest(model="mock-fast", messages=[])

    def test_no_user_message_raises(self) -> None:
        """At least one message must have role='user'."""
        with pytest.raises(ValidationError, match="user"):
            ChatCompletionRequest(
                model="mock-fast",
                messages=[{"role": "system", "content": "You are helpful."}],
            )

    def test_user_message_empty_content_raises(self) -> None:
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="mock-fast",
                messages=[{"role": "user", "content": ""}],
            )

    def test_temperature_out_of_range_raises(self) -> None:
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="mock-fast",
                messages=[{"role": "user", "content": "hi"}],
                temperature=3.0,  # > 2.0
            )

    def test_negative_temperature_raises(self) -> None:
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="mock-fast",
                messages=[{"role": "user", "content": "hi"}],
                temperature=-0.1,
            )

    def test_max_tokens_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="mock-fast",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=0,
            )

    def test_invalid_role_raises(self) -> None:
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="mock-fast",
                messages=[{"role": "invalid_role", "content": "hi"}],
            )


# ---------------------------------------------------------------------------
# ChatCompletionResponse
# ---------------------------------------------------------------------------


class TestChatCompletionResponse:
    def test_valid_response_construction(self) -> None:
        resp = ChatCompletionResponse(
            model="mock-fast",
            choices=[
                Choice(
                    index=0,
                    message=ChoiceMessage(content="Hello!"),
                    finish_reason="stop",
                )
            ],
            usage=UsageInfo(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
        )
        assert resp.object == "chat.completion"
        assert resp.model == "mock-fast"
        assert len(resp.choices) == 1
        assert resp.choices[0].message.content == "Hello!"
        assert resp.usage.total_tokens == 15

    def test_id_auto_generated(self) -> None:
        resp = ChatCompletionResponse(
            model="mock-fast",
            choices=[Choice(index=0, message=ChoiceMessage(content="Hi"), finish_reason="stop")],
            usage=UsageInfo(),
        )
        assert resp.id.startswith("chatcmpl-")

    def test_created_is_unix_timestamp(self) -> None:
        import time

        resp = ChatCompletionResponse(
            model="mock-fast",
            choices=[Choice(index=0, message=ChoiceMessage(content="Hi"), finish_reason="stop")],
            usage=UsageInfo(),
        )
        assert abs(resp.created - int(time.time())) < 5


# ---------------------------------------------------------------------------
# OpenAIErrorResponse
# ---------------------------------------------------------------------------


class TestOpenAIErrorResponse:
    def test_bad_request_factory(self) -> None:
        err = OpenAIErrorResponse.bad_request("Something went wrong")
        assert err.error.type == "invalid_request_error"
        assert err.error.code == "invalid_request"
        assert "Something went wrong" in err.error.message

    def test_streaming_not_supported_factory(self) -> None:
        err = OpenAIErrorResponse.streaming_not_supported()
        assert err.error.code == "streaming_not_supported"
        assert "stream" in err.error.message.lower()

    def test_model_not_found_factory(self) -> None:
        err = OpenAIErrorResponse.model_not_found("gpt-99")
        assert "gpt-99" in err.error.message
        assert err.error.code == "model_not_found"

    def test_error_serializes_to_dict(self) -> None:
        err = OpenAIErrorResponse.bad_request("oops")
        d = err.model_dump()
        assert "error" in d
        assert d["error"]["message"] == "oops"
