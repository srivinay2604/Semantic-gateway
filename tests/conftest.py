"""Shared pytest fixtures for CacheGate tests.

Provides:
- ``app``: the FastAPI ASGI app (lifespan managed by pytest-asyncio).
- ``client``: an httpx.AsyncClient against the ASGI app.
- ``mock_provider``: a zero-latency, zero-error-rate MockProvider.
- ``provider_router``: a ProviderRouter with the real models.yaml.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from cachegate.main import create_app
from cachegate.providers.mock import MockProvider
from cachegate.providers.router import ProviderRouter
from cachegate.settings import settings


@pytest.fixture(autouse=True, scope="session")
def zero_latency_for_tests() -> None:
    """Set zero latency for mock provider by default in tests."""
    settings.mock_latency_ms = 0
    settings.mock_latency_jitter_ms = 0


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture()
async def app() -> AsyncGenerator:
    """Yield a fresh FastAPI app instance for each test."""
    application = create_app()
    # Manually trigger lifespan by using ASGI lifespan protocol
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture()
async def client(app: object) -> AsyncGenerator[AsyncClient, None]:
    """Yield an httpx.AsyncClient connected to the ASGI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://testserver",
    ) as ac:
        yield ac


@pytest.fixture()
def mock_provider_zero_latency() -> MockProvider:
    """A MockProvider with zero latency and zero error rate for fast unit tests."""
    return MockProvider(latency_ms=0, jitter_ms=0, error_rate=0.0)


@pytest.fixture()
def mock_provider_with_errors() -> MockProvider:
    """A MockProvider that always errors (error_rate=1.0)."""
    return MockProvider(latency_ms=0, jitter_ms=0, error_rate=1.0)


@pytest.fixture()
def provider_router(tmp_path: object) -> ProviderRouter:
    """A ProviderRouter loaded from the real config/models.yaml."""
    return ProviderRouter(models_config_path="config/models.yaml")
