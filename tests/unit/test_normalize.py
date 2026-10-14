"""Unit tests for normalization and canonical serialization."""

from __future__ import annotations

from cachegate.cache.normalize import (
    canonical_request_dict,
    canonical_request_json,
    extract_context_messages,
    extract_last_user_message,
    extract_system_prompt,
    normalize_text,
)
from cachegate.schemas.internal import InternalMessage, InternalRequest
from cachegate.schemas.openai import ChatCompletionRequest, ChatMessage


def test_normalize_text() -> None:
    assert normalize_text("  Hello   World!\n\t ") == "hello world!"
    assert normalize_text("UPPERCASE") == "uppercase"


def test_canonical_request_dict_ignores_non_output_fields() -> None:
    req = InternalRequest(
        request_id="test-uuid",
        model_alias="gpt-4o-mini",
        resolved_provider="openai/gpt-4o-mini",
        messages=[InternalMessage(role="user", content="Hello")],
        temperature=0.0,
        top_p=1.0,
        max_tokens=100,
        stop=["END"],
        user="test-user-id",  # should be excluded
    )
    d = canonical_request_dict(req)
    assert d["model"] == "gpt-4o-mini"
    assert d["temperature"] == 0.0
    assert d["top_p"] == 1.0
    assert d["max_tokens"] == 100
    assert d["stop"] == ["END"]
    assert "user" not in d
    assert "request_id" not in d
    assert "resolved_provider" not in d


def test_canonical_request_json_is_deterministic() -> None:
    req1 = ChatCompletionRequest(
        model="gpt-4o-mini",
        messages=[ChatMessage(role="user", content="Test")],
        temperature=0.2,
        user="alice",
    )
    req2 = ChatCompletionRequest(
        model="gpt-4o-mini",
        messages=[ChatMessage(role="user", content="Test")],
        temperature=0.2,
        user="bob",  # different user, same output
    )
    assert canonical_request_json(req1) == canonical_request_json(req2)


def test_extract_message_helpers() -> None:
    messages = [
        InternalMessage(role="system", content="You are a helper."),
        InternalMessage(role="user", content="Message 1"),
        InternalMessage(role="assistant", content="Answer 1"),
        InternalMessage(role="user", content="Message 2"),
    ]
    assert extract_system_prompt(messages) == "You are a helper."
    assert extract_last_user_message(messages) == "Message 2"
    ctx = extract_context_messages(messages)
    assert len(ctx) == 2
    assert ctx[0]["role"] == "user"
    assert ctx[1]["role"] == "assistant"
