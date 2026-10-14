"""Provider interface (ABC) for CacheGate.

Every provider adapter must implement this interface. The design ensures that:
1. Non-streaming calls (Phase 1) work now.
2. Streaming calls (Phase 2) can be added by implementing ``stream_complete``
   without breaking existing consumers.

Usage::
    from cachegate.providers.base import Provider
    class MyProvider(Provider):
        async def complete(self, request: InternalRequest) -> InternalResponse:
            ...
        async def stream_complete(self, request: InternalRequest):
            ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cachegate.schemas.internal import InternalRequest, InternalResponse


class Provider(ABC):
    """Abstract base class for LLM provider adapters.

    Implementations:
    - ``cachegate.providers.mock.MockProvider``   — Phase 1
    - ``cachegate.providers.openai.OpenAIProvider``  — Phase 2
    - ``cachegate.providers.anthropic.AnthropicProvider``  — Phase 2

    Design contract:
    - All methods are async; never block the event loop.
    - Raise :class:`ProviderError` (or a subclass) on unrecoverable failures so
      the router can apply retries / circuit breaker / fallback logic.
    - ``stream_complete`` is a stub in Phase 1; raise ``NotImplementedError``
      until Phase 2 fills it in. The router checks ``provider.supports_streaming``
      before calling it.
    """

    #: Set to True in providers that implement stream_complete (Phase 2+).
    supports_streaming: bool = False

    @abstractmethod
    async def complete(self, request: InternalRequest) -> InternalResponse:
        """Perform a non-streaming chat completion.

        Args:
            request: The normalised internal request.

        Returns:
            An :class:`InternalResponse` populated with the provider's answer.

        Raises:
            ProviderError: On any unrecoverable upstream failure.
        """
        ...

    async def stream_complete(
        self, request: InternalRequest
    ) -> AsyncIterator[str]:  # pragma: no cover
        """Streaming chat completion — implemented in Phase 2.

        Each yielded string is a raw SSE ``data: ...`` line (without the
        ``data: `` prefix). The final chunk is ``[DONE]``.

        Raises:
            NotImplementedError: Until Phase 2 is implemented.
        """
        raise NotImplementedError("Streaming is not implemented until Phase 2.")
        # This unreachable yield turns the method into a generator so subclasses
        # can ``yield`` without syntax errors.
        yield ""  # pragma: no cover

    async def health_check(self) -> bool:
        """Return True if the provider is reachable. Default: always True.

        Override in real provider adapters (Phase 2+).
        """
        return True


# ---------------------------------------------------------------------------
# Provider error hierarchy
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Base class for provider errors.

    Attributes:
        status_code: The upstream HTTP status code, if applicable.
        retryable: Whether the router should retry this error.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 500,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class ProviderRateLimitError(ProviderError):
    """429 Too Many Requests from the upstream provider."""

    def __init__(self, message: str = "Upstream rate limit exceeded") -> None:
        super().__init__(message, status_code=429, retryable=True)


class ProviderTimeoutError(ProviderError):
    """Upstream provider timed out."""

    def __init__(self, message: str = "Upstream provider timed out") -> None:
        super().__init__(message, status_code=504, retryable=True)


class ProviderUnavailableError(ProviderError):
    """Upstream provider returned 5xx."""

    def __init__(self, message: str = "Upstream provider unavailable") -> None:
        super().__init__(message, status_code=502, retryable=True)
