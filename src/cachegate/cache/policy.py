"""Cacheability policy rules according to spec §9.4.

Rules for request caching eligibility:
  1. Temperature <= CACHE_MAX_TEMPERATURE (default 0.3).
  2. No tools or tool calls in the request.
  3. Client headers do not request bypass (Cache-Control: no-cache, X-Cache-Bypass: true).
  4. Prompt does not trigger time-sensitivity heuristic.

Rules for response caching eligibility:
  1. Response finish_reason must be 'stop'.
  2. Content must be non-empty string.
  3. No tool_calls in the response.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from cachegate.schemas.internal import InternalRequest, InternalResponse

# Heuristic patterns for time-sensitive prompts
TIME_SENSITIVE_PATTERNS = re.compile(
    r"\b(today(?:'s)?|right now|current time|current date|what time is it|"
    r"latest news|breaking news|live score|stock price)\b",
    re.IGNORECASE,
)


def is_request_cacheable(
    request: InternalRequest,
    headers: Mapping[str, str] | None = None,
    max_temperature: float = 0.3,
    check_time_sensitive: bool = True,
) -> tuple[bool, str | None]:
    """Evaluate whether an incoming request is eligible for cache lookup and storage.

    Returns:
        (True, None) if cacheable, or (False, reason_str) if disqualified.
    """
    # 1. Header bypass checks
    if headers:
        # Standard HTTP Cache-Control header
        cc = headers.get("cache-control", "").lower()
        if "no-cache" in cc or "no-store" in cc:
            return False, "cache_control_no_cache"

        # Explicit gateway bypass header
        bypass = headers.get("x-cache-bypass", "").lower()
        if bypass in ("true", "1", "yes"):
            return False, "x_cache_bypass"

    # 2. Temperature threshold check
    if request.temperature is not None and request.temperature > max_temperature:
        return False, f"temperature_exceeds_threshold:{request.temperature}>{max_temperature}"

    # 3. Time-sensitivity heuristic
    if check_time_sensitive:
        for msg in request.messages:
            if msg.content and TIME_SENSITIVE_PATTERNS.search(msg.content):
                return False, "time_sensitive_prompt"

    return True, None


def is_response_cacheable(
    response: InternalResponse,
    extra_metadata: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    """Evaluate whether an upstream provider response is eligible to be cached.

    Returns:
        (True, None) if eligible to store, or (False, reason_str) if disqualified.
    """
    if response.finish_reason != "stop":
        return False, f"finish_reason_not_stop:{response.finish_reason}"

    if not response.content or not response.content.strip():
        return False, "empty_content"

    if extra_metadata and extra_metadata.get("tool_calls"):
        return False, "response_contains_tool_calls"

    return True, None
