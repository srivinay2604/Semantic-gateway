"""Unit tests for cacheability policy."""

from __future__ import annotations

from cachegate.cache.policy import is_request_cacheable, is_response_cacheable
from cachegate.schemas.internal import (
    InternalMessage,
    InternalRequest,
    InternalResponse,
    TokenUsage,
)


def _make_req(temperature: float = 0.0, prompt: str = "Explain quantum physics") -> InternalRequest:
    return InternalRequest(
        request_id="req-1",
        model_alias="gpt-4o-mini",
        resolved_provider="openai/gpt-4o-mini",
        messages=[InternalMessage(role="user", content=prompt)],
        temperature=temperature,
    )


def test_request_cacheable_default() -> None:
    req = _make_req(temperature=0.2)
    cacheable, reason = is_request_cacheable(req, max_temperature=0.3)
    assert cacheable is True
    assert reason is None


def test_request_disqualified_by_temperature() -> None:
    req = _make_req(temperature=0.7)
    cacheable, reason = is_request_cacheable(req, max_temperature=0.3)
    assert cacheable is False
    assert "temperature_exceeds_threshold" in str(reason)


def test_request_disqualified_by_cache_control_header() -> None:
    req = _make_req(temperature=0.0)
    cacheable, reason = is_request_cacheable(req, headers={"cache-control": "no-cache"})
    assert cacheable is False
    assert reason == "cache_control_no_cache"


def test_request_disqualified_by_bypass_header() -> None:
    req = _make_req(temperature=0.0)
    cacheable, reason = is_request_cacheable(req, headers={"x-cache-bypass": "true"})
    assert cacheable is False
    assert reason == "x_cache_bypass"


def test_request_disqualified_by_time_sensitive_prompt() -> None:
    req = _make_req(temperature=0.0, prompt="What is today's stock price for AAPL?")
    cacheable, reason = is_request_cacheable(req)
    assert cacheable is False
    assert reason == "time_sensitive_prompt"


def test_response_cacheability() -> None:
    valid_resp = InternalResponse(
        response_id="r-1",
        model="gpt-4o-mini",
        content="Here is the explanation.",
        finish_reason="stop",
        provider="openai/gpt-4o-mini",
        usage=TokenUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
    )
    ok, reason = is_response_cacheable(valid_resp)
    assert ok is True
    assert reason is None

    # Disqualified if finish_reason != "stop"
    truncated_resp = InternalResponse(
        response_id="r-2",
        model="gpt-4o-mini",
        content="Truncated...",
        finish_reason="length",
        provider="openai/gpt-4o-mini",
        usage=TokenUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
    )
    ok, reason = is_response_cacheable(truncated_resp)
    assert ok is False
    assert "finish_reason_not_stop" in str(reason)

    # Disqualified if content is empty
    empty_resp = InternalResponse(
        response_id="r-3",
        model="gpt-4o-mini",
        content="",
        finish_reason="stop",
        provider="openai/gpt-4o-mini",
        usage=TokenUsage(prompt_tokens=5, completion_tokens=0, total_tokens=5),
    )
    ok, reason = is_response_cacheable(empty_resp)
    assert ok is False
    assert reason == "empty_content"
