"""Provider router — resolves model aliases to provider instances and fallback chains.

Reads ``config/models.yaml`` at startup (via :func:`load_router`) and maps
each model alias to the appropriate :class:`~cachegate.providers.base.Provider`
implementation and fallback chain.

Phase 3 Resilience:
- Circuit breaker per provider (closed, open, half-open).
- Automatic retries with exponential backoff + jitter for transient failures.
- Multi-provider fallback chain execution when primary fails or is circuit-broken.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import structlog
import yaml

from cachegate.providers.anthropic import AnthropicProvider
from cachegate.providers.base import Provider, ProviderError
from cachegate.providers.circuit_breaker import CircuitBreaker
from cachegate.providers.mock import MockProvider
from cachegate.providers.openai import OpenAIProvider
from cachegate.providers.retry import execute_with_retry
from cachegate.schemas.internal import InternalRequest, InternalResponse
from cachegate.settings import settings

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_models_yaml(path: str) -> dict[str, Any]:
    """Load and parse models.yaml, returning the ``models`` dict."""
    yaml_path = Path(path)
    if not yaml_path.exists():
        logger.warning("models_yaml_not_found", path=str(yaml_path))
        return {}
    with yaml_path.open() as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        return {}
    models = data.get("models")
    if isinstance(models, dict):
        return {str(k): v for k, v in models.items()}
    return {}


def _resolve_provider(provider_string: str) -> Provider:
    """Instantiate a provider from a ``"<name>/<model>"`` string.

    Supported:
    - ``mock/*`` -> MockProvider
    - ``openai/<model>`` -> OpenAIProvider(model=<model>)
    - ``anthropic/<model>`` -> AnthropicProvider(model=<model>)
    """
    parts = provider_string.split("/", 1)
    name_part = parts[0].lower()
    model_part = parts[1] if len(parts) > 1 else ""

    if name_part == "mock":
        return MockProvider()
    if name_part == "openai":
        return OpenAIProvider(model=model_part or "gpt-4o-mini")
    if name_part == "anthropic":
        return AnthropicProvider(model=model_part or "claude-3-haiku-20240307")
    if name_part == "ollama":
        return OpenAIProvider(
            model=model_part or "llama3.2",
            api_key="ollama",
            base_url=settings.ollama_base_url,
            timeout_seconds=60.0,
        )

    logger.warning(
        "unknown_provider_falling_back_to_mock",
        provider_string=provider_string,
    )
    return MockProvider()


# ---------------------------------------------------------------------------
# ProviderRouter
# ---------------------------------------------------------------------------


class ProviderRouter:
    """Resolves model aliases to providers with circuit breakers and fallback chains.

    Args:
        models_config_path: Path to ``config/models.yaml``.
    """

    def __init__(self, models_config_path: str | None = None) -> None:
        path = models_config_path or settings.models_config_path
        self._models: dict[str, Any] = _load_models_yaml(path)
        # Cache resolved Provider instances to avoid re-instantiating on every request
        self._cache: dict[str, Provider] = {}
        # Circuit breakers tracked per resolved provider string
        self._breakers: dict[str, CircuitBreaker] = {}
        logger.info("provider_router_loaded", num_aliases=len(self._models))

    def get_breaker(self, provider_str: str) -> CircuitBreaker:
        """Get or initialize the CircuitBreaker for a specific provider string."""
        if provider_str not in self._breakers:
            self._breakers[provider_str] = CircuitBreaker(provider_name=provider_str)
        return self._breakers[provider_str]

    def resolve(self, model_alias: str) -> tuple[Provider, str]:
        """Return the primary provider and provider string for an alias."""
        chain = self.resolve_chain(model_alias)
        return chain[0]

    def resolve_chain(self, model_alias: str) -> list[tuple[Provider, str]]:
        """Return the ordered list of (provider, provider_str) for primary and fallbacks."""
        alias_config = self._models.get(model_alias)
        if alias_config is None:
            logger.warning(
                "model_alias_not_found_using_mock",
                model_alias=model_alias,
            )
            provider_strings = ["mock/echo"]
        else:
            primary = alias_config.get("primary", "mock/echo")
            raw_fallbacks = alias_config.get("fallbacks", [])
            fallbacks = [f for f in raw_fallbacks if f != primary]
            provider_strings = [primary] + fallbacks

        chain: list[tuple[Provider, str]] = []
        for p_str in provider_strings:
            if p_str not in self._cache:
                self._cache[p_str] = _resolve_provider(p_str)
            chain.append((self._cache[p_str], p_str))
        return chain

    async def execute_with_resilience(
        self, request: InternalRequest
    ) -> tuple[InternalResponse, str]:
        """Execute across fallback chain with circuit breakers and retries.

        Returns:
            A tuple of (InternalResponse, actual_provider_str).

        Raises:
            ProviderError: If all providers in the chain fail or are circuit-broken.
        """
        chain = self.resolve_chain(request.model_alias)
        last_error: ProviderError | None = None

        for provider, provider_str in chain:
            breaker = self.get_breaker(provider_str)
            if not await breaker.allow_request():
                logger.warning(
                    "circuit_breaker_open_skipping_provider",
                    model_alias=request.model_alias,
                    provider=provider_str,
                    recovery_remaining_seconds=round(breaker.remaining_recovery_seconds(), 1),
                )
                continue

            try:
                logger.debug(
                    "calling_provider_with_retry",
                    model_alias=request.model_alias,
                    provider=provider_str,
                )
                response = await execute_with_retry(provider.complete, request)
                await breaker.record_success()
                response.provider = provider_str
                return response, provider_str
            except ProviderError as exc:
                if exc.retryable:
                    await breaker.record_failure()
                logger.warning(
                    "provider_call_failed_attempting_fallback",
                    model_alias=request.model_alias,
                    provider=provider_str,
                    status_code=exc.status_code,
                    error=str(exc),
                )
                last_error = exc
                continue

        if last_error is not None:
            raise last_error
        msg = f"All providers in fallback chain failed for '{request.model_alias}'"
        raise ProviderError(msg, status_code=502)

    async def stream_with_resilience(
        self, request: InternalRequest
    ) -> tuple[AsyncIterator[str], str]:
        """Establish a streaming completion with fallback if primary fails to start."""
        chain = self.resolve_chain(request.model_alias)
        last_error: ProviderError | None = None

        for provider, provider_str in chain:
            if not provider.supports_streaming:
                continue

            breaker = self.get_breaker(provider_str)
            if not await breaker.allow_request():
                logger.warning(
                    "circuit_breaker_open_skipping_stream_provider",
                    model_alias=request.model_alias,
                    provider=provider_str,
                )
                continue

            try:
                gen = provider.stream_complete(request)
                first_chunk = await anext(gen)
            except ProviderError as exc:
                if exc.retryable:
                    await breaker.record_failure()
                logger.warning(
                    "stream_provider_start_failed_attempting_fallback",
                    model_alias=request.model_alias,
                    provider=provider_str,
                    error=str(exc),
                )
                last_error = exc
                continue
            except StopAsyncIteration:
                await breaker.record_success()

                async def _empty() -> AsyncIterator[str]:
                    yield "[DONE]"

                return _empty(), provider_str
            except Exception as exc:
                await breaker.record_failure()
                logger.warning(
                    "unexpected_stream_start_failure",
                    provider=provider_str,
                    error=str(exc),
                )
                last_error = ProviderError(f"Stream start error: {exc}", status_code=502)
                continue

            await breaker.record_success()

            async def _stream_wrapper(
                head: str = first_chunk, tail: AsyncIterator[str] = gen
            ) -> AsyncIterator[str]:
                yield head
                async for chunk in tail:
                    yield chunk

            return _stream_wrapper(), provider_str

        if last_error is not None:
            raise last_error
        stream_err = f"All providers in fallback chain failed for streaming '{request.model_alias}'"
        raise ProviderError(stream_err, status_code=502)

    def list_aliases(self) -> list[str]:
        """Return all configured model aliases."""
        return list(self._models.keys())

    def has_alias(self, model_alias: str) -> bool:
        """Check whether an alias is explicitly configured."""
        return model_alias in self._models
