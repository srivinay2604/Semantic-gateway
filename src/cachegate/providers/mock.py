"""MockProvider — deterministic, zero-cost LLM provider for testing and development.

Features:
- Deterministic response derived from a hash of the input messages, so tests
  can assert on exact output without flakiness.
- Configurable artificial latency (base + ±jitter) via settings.
- Configurable error rate to test resilience logic.
- Realistic token counts using tiktoken (cl100k_base tokenizer).

The reply always echoes the last user message and adds a canned suffix so it
is easy to verify routing end-to-end.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
import uuid
from collections.abc import Sequence

import structlog

from cachegate.providers.base import Provider, ProviderError
from cachegate.schemas.internal import (
    InternalMessage,
    InternalRequest,
    InternalResponse,
    InternalUsage,
)
from cachegate.settings import settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Tiktoken: optional import (graceful fallback if somehow unavailable)
# ---------------------------------------------------------------------------
try:
    import tiktoken

    _ENCODING = tiktoken.get_encoding("cl100k_base")

    def _count_tokens(text: str) -> int:
        return len(_ENCODING.encode(text))

except Exception:  # pragma: no cover
    logger.warning("tiktoken unavailable; using character-count fallback for token estimation")

    def _count_tokens(text: str) -> int:
        return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Canned response pool
# ---------------------------------------------------------------------------
_CANNED_RESPONSES = [
    "I understand your question. {echo} — Here is a helpful response from the mock provider.",
    "Thank you for asking about '{echo}'. The mock provider acknowledges your message.",
    "Regarding '{echo}': this is a deterministic mock response for testing purposes.",
    "The mock provider received: '{echo}'. All systems are functioning correctly.",
    "Mock response for '{echo}': CacheGate is routing your request successfully.",
]


def _build_reply(messages: Sequence[InternalMessage], seed: int) -> str:
    """Build a deterministic reply seeded by the request content hash.

    Args:
        messages: The list of InternalMessage objects.
        seed: Integer seed derived from hashing the request content.

    Returns:
        A short canned response string that echoes the last user message.
    """
    last_user_content = ""
    for msg in reversed(messages):
        if msg.role == "user" and msg.content:
            last_user_content = msg.content[:80]
            break

    template = _CANNED_RESPONSES[seed % len(_CANNED_RESPONSES)]
    return template.format(echo=last_user_content)


def _request_seed(request: InternalRequest) -> int:
    """Compute a deterministic integer seed from the request messages."""
    content_parts = []
    for msg in request.messages:
        content_parts.append(f"{msg.role}:{msg.content or ''}")
    digest = hashlib.sha256("\n".join(content_parts).encode()).hexdigest()
    # Take the first 8 hex chars as a 32-bit integer
    return int(digest[:8], 16)


class MockProvider(Provider):
    """Deterministic mock LLM provider for Phase 1 testing and load testing.

    Configuration (all from :class:`~cachegate.settings.Settings`):
    - ``mock_latency_ms``: base artificial latency in milliseconds.
    - ``mock_latency_jitter_ms``: ±jitter added to base latency.
    - ``mock_error_rate``: probability [0, 1] of raising a :class:`ProviderError`.

    These can be overridden per-instance via the constructor for fine-grained
    test control.
    """

    supports_streaming: bool = False  # Phase 2 will add streaming

    def __init__(
        self,
        latency_ms: int | None = None,
        jitter_ms: int | None = None,
        error_rate: float | None = None,
    ) -> None:
        """Create a MockProvider.

        Args:
            latency_ms: Override for base latency. Defaults to ``settings.mock_latency_ms``.
            jitter_ms: Override for jitter. Defaults to ``settings.mock_latency_jitter_ms``.
            error_rate: Override for error probability. Defaults to ``settings.mock_error_rate``.
        """
        self._latency_ms = latency_ms if latency_ms is not None else settings.mock_latency_ms
        self._jitter_ms = jitter_ms if jitter_ms is not None else settings.mock_latency_jitter_ms
        self._error_rate = error_rate if error_rate is not None else settings.mock_error_rate

    def _sleep_seconds(self) -> float:
        """Compute the total sleep duration in seconds including jitter."""
        jitter = random.uniform(-self._jitter_ms, self._jitter_ms)
        return max(0.0, (self._latency_ms + jitter)) / 1000.0

    async def complete(self, request: InternalRequest) -> InternalResponse:
        """Return a deterministic mock completion after an artificial delay.

        Args:
            request: Normalised internal request.

        Returns:
            A populated :class:`InternalResponse`.

        Raises:
            ProviderError: With probability ``error_rate`` to simulate failures.
        """
        # Simulate network latency
        delay = self._sleep_seconds()
        t0 = time.monotonic()
        await asyncio.sleep(delay)

        # Simulate random errors
        if self._error_rate > 0 and random.random() < self._error_rate:
            logger.warning(
                "mock_provider_simulated_error",
                request_id=request.request_id,
                error_rate=self._error_rate,
            )
            raise ProviderError(
                "Mock provider simulated error",
                status_code=500,
                retryable=True,
            )

        seed = _request_seed(request)
        reply_content = _build_reply(request.messages, seed)

        # Count tokens
        prompt_text = "\n".join(f"{m.role}: {m.content or ''}" for m in request.messages)
        prompt_tokens = _count_tokens(prompt_text)
        completion_tokens = _count_tokens(reply_content)

        provider_latency_ms = (time.monotonic() - t0) * 1000

        logger.debug(
            "mock_provider_response",
            request_id=request.request_id,
            model=request.model_alias,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=round(provider_latency_ms, 1),
        )

        return InternalResponse(
            response_id=f"mock-{uuid.uuid4().hex}",
            model="mock/echo",
            content=reply_content,
            finish_reason="stop",
            usage=InternalUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            provider_latency_ms=provider_latency_ms,
            provider="mock",
        )

    async def health_check(self) -> bool:
        """Mock provider is always healthy."""
        return True
