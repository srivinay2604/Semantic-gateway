"""Provider router — resolves model aliases to provider instances.

Reads ``config/models.yaml`` at startup (via :func:`load_router`) and maps
each model alias to the appropriate :class:`~cachegate.providers.base.Provider`
implementation.

Phase 1: only ``mock/echo`` is supported; all aliases route to
:class:`~cachegate.providers.mock.MockProvider`.

Phase 2: adds ``openai/*`` and ``anthropic/*`` providers.
Phase 3: adds fallback chain iteration and circuit breaker integration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml

from cachegate.providers.anthropic import AnthropicProvider
from cachegate.providers.base import Provider
from cachegate.providers.mock import MockProvider
from cachegate.providers.openai import OpenAIProvider
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

    Supported in Phase 2:
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

    logger.warning(
        "unknown_provider_falling_back_to_mock",
        provider_string=provider_string,
    )
    return MockProvider()


# ---------------------------------------------------------------------------
# ProviderRouter
# ---------------------------------------------------------------------------


class ProviderRouter:
    """Resolves a model alias to a :class:`Provider` instance.

    Args:
        models_config_path: Path to ``config/models.yaml``.

    The router is loaded once at app startup via the lifespan context and
    stored on ``app.state.provider_router``.

    Fallback chain (Phase 3):
        Currently stubbed — the router always uses the primary provider.
        Phase 3 will add iteration over ``fallbacks`` with circuit breaker checks.
    """

    def __init__(self, models_config_path: str | None = None) -> None:
        path = models_config_path or settings.models_config_path
        self._models: dict[str, Any] = _load_models_yaml(path)
        # Cache resolved Provider instances to avoid re-instantiating on every request
        self._cache: dict[str, Provider] = {}
        logger.info("provider_router_loaded", num_aliases=len(self._models))

    def resolve(self, model_alias: str) -> tuple[Provider, str]:
        """Return the provider and resolved provider string for a model alias.

        Args:
            model_alias: The model alias submitted by the client.

        Returns:
            A ``(provider, provider_string)`` tuple.
            If the alias is not found, falls back to ``mock/echo`` with a warning.
        """
        alias_config = self._models.get(model_alias)
        if alias_config is None:
            logger.warning(
                "model_alias_not_found_using_mock",
                model_alias=model_alias,
            )
            provider_str = "mock/echo"
        else:
            provider_str = alias_config.get("primary", "mock/echo")

        if provider_str not in self._cache:
            self._cache[provider_str] = _resolve_provider(provider_str)

        return self._cache[provider_str], provider_str

    def list_aliases(self) -> list[str]:
        """Return all configured model aliases."""
        return list(self._models.keys())

    def has_alias(self, model_alias: str) -> bool:
        """Check whether an alias is explicitly configured."""
        return model_alias in self._models
