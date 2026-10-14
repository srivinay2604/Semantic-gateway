"""Unit tests for ProviderRouter.

Tests cover:
- Alias resolution for configured models.
- Fallback to mock for unknown aliases.
- list_aliases() returning all configured aliases.
- has_alias() correctness.
- Provider caching (same instance returned on second call).
"""

from __future__ import annotations

from cachegate.providers.mock import MockProvider
from cachegate.providers.router import ProviderRouter


class TestProviderRouterAliasResolution:
    def test_resolves_mock_fast(self, provider_router: ProviderRouter) -> None:
        provider, provider_str = provider_router.resolve("mock-fast")
        assert isinstance(provider, MockProvider)
        assert provider_str == "mock/echo"

    def test_resolves_gpt_4o_mini_to_mock(self, provider_router: ProviderRouter) -> None:
        """In Phase 1, gpt-4o-mini is mapped to mock/echo."""
        provider, provider_str = provider_router.resolve("gpt-4o-mini")
        assert isinstance(provider, MockProvider)
        assert "mock" in provider_str

    def test_resolves_claude_haiku_to_mock(self, provider_router: ProviderRouter) -> None:
        """In Phase 1, claude-haiku is mapped to mock/echo."""
        provider, provider_str = provider_router.resolve("claude-haiku")
        assert isinstance(provider, MockProvider)

    def test_unknown_alias_falls_back_to_mock(self, provider_router: ProviderRouter) -> None:
        """An unknown alias should fall back to mock/echo with a warning."""
        provider, provider_str = provider_router.resolve("non-existent-model-xyz")
        assert isinstance(provider, MockProvider)
        assert "mock" in provider_str

    def test_provider_instance_is_cached(self, provider_router: ProviderRouter) -> None:
        """Resolving the same provider string twice should return the same instance."""
        p1, _ = provider_router.resolve("mock-fast")
        p2, _ = provider_router.resolve("mock-fast")
        assert p1 is p2

    def test_different_aliases_same_provider_may_share_instance(
        self, provider_router: ProviderRouter
    ) -> None:
        """If two aliases resolve to the same provider string, they share an instance."""
        p1, str1 = provider_router.resolve("mock-fast")
        p2, str2 = provider_router.resolve("gpt-4o-mini")
        # Both are mock/echo in Phase 1
        if str1 == str2:
            assert p1 is p2


class TestProviderRouterListing:
    def test_list_aliases_returns_configured_models(self, provider_router: ProviderRouter) -> None:
        aliases = provider_router.list_aliases()
        assert "mock-fast" in aliases
        assert "gpt-4o-mini" in aliases

    def test_list_aliases_is_list_of_strings(self, provider_router: ProviderRouter) -> None:
        aliases = provider_router.list_aliases()
        assert isinstance(aliases, list)
        assert all(isinstance(a, str) for a in aliases)

    def test_has_alias_true_for_configured(self, provider_router: ProviderRouter) -> None:
        assert provider_router.has_alias("mock-fast") is True

    def test_has_alias_false_for_unknown(self, provider_router: ProviderRouter) -> None:
        assert provider_router.has_alias("definitely-not-a-model") is False


class TestProviderRouterMissingConfig:
    def test_missing_yaml_returns_empty_router(self, tmp_path: object) -> None:
        """Router with a non-existent config path should gracefully return an empty model list."""
        router = ProviderRouter(models_config_path="/nonexistent/path/models.yaml")
        aliases = router.list_aliases()
        assert aliases == []

    def test_missing_yaml_still_resolves_to_mock(self, tmp_path: object) -> None:
        """Even with no config, resolving any alias falls back to mock."""
        router = ProviderRouter(models_config_path="/nonexistent/path/models.yaml")
        provider, provider_str = router.resolve("anything")
        assert isinstance(provider, MockProvider)
