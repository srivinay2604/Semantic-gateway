"""API integration and acceptance tests for Phase 5 Semantic Cache."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from cachegate.cache.manager import CacheManager
from cachegate.cache.stores.memory_store import MemoryExactStore, MemoryVectorStore
from cachegate.embeddings.sbert import MockEmbedder
from cachegate.main import create_app
from cachegate.schemas.internal import InternalResponse, TokenUsage
from cachegate.settings import Settings

CHAT_URL = "/v1/chat/completions"


class TestSemanticCacheAPI:
    @pytest.mark.asyncio
    async def test_semantic_cache_paraphrase_hit(self) -> None:
        """Paraphrased question returns HIT-SEMANTIC with similarity in the header,

        and skips upstream provider.
        """
        app = create_app()
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(
            return_value=InternalResponse(
                response_id="mock-1",
                model="mock-fast",
                content="To reset your password, visit account settings.",
                finish_reason="stop",
                provider="mock/echo",
                usage=TokenUsage(prompt_tokens=8, completion_tokens=8, total_tokens=16),
            )
        )
        mock_provider.supports_streaming = True

        async with app.router.lifespan_context(app):
            # Configure with lower threshold to test paraphrase hit cleanly
            custom_settings = Settings(semantic_threshold=0.85)
            memory_exact = MemoryExactStore()
            memory_vector = MemoryVectorStore()
            embedder = MockEmbedder(dim=384)
            manager = CacheManager(
                exact_store=memory_exact,
                vector_store=memory_vector,
                embedder=embedder,
                settings=custom_settings,
            )
            app.state.cache_manager = manager
            app.state.provider_router._cache["mock/echo"] = mock_provider

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                # 1. First request: initial phrasing -> MISS
                req1 = {
                    "model": "mock-fast",
                    "messages": [
                        {"role": "user", "content": "How do I reset my account password?"}
                    ],
                    "temperature": 0.0,
                }
                resp1 = await client.post(CHAT_URL, json=req1)
                assert resp1.status_code == 200
                assert resp1.headers["x-cache"] == "MISS"
                assert mock_provider.complete.call_count == 1

                # 2. Paraphrased request: different wording, same intent -> HIT-SEMANTIC
                req2 = {
                    "model": "mock-fast",
                    "messages": [
                        {"role": "user", "content": "How can I reset my account password?"}
                    ],
                    "temperature": 0.0,
                }
                resp2 = await client.post(CHAT_URL, json=req2)
                assert resp2.status_code == 200
                assert resp2.headers["x-cache"] == "HIT-SEMANTIC"
                assert "x-cache-similarity" in resp2.headers
                similarity = float(resp2.headers["x-cache-similarity"])
                assert similarity >= 0.85
                # Provider was skipped for paraphrase!
                assert mock_provider.complete.call_count == 1
                assert (
                    resp2.json()["choices"][0]["message"]["content"]
                    == "To reset your password, visit account settings."
                )

    @pytest.mark.asyncio
    async def test_partition_isolation_different_system_prompt_misses(self) -> None:
        """A request with a different system prompt must not hit semantic cache."""
        app = create_app()
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(
            return_value=InternalResponse(
                response_id="mock-1",
                model="mock-fast",
                content="System prompt answer.",
                finish_reason="stop",
                provider="mock/echo",
                usage=TokenUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
            )
        )
        mock_provider.supports_streaming = True

        async with app.router.lifespan_context(app):
            app.state.cache_manager = CacheManager(
                exact_store=MemoryExactStore(),
                vector_store=MemoryVectorStore(),
                embedder=MockEmbedder(dim=384),
                settings=Settings(semantic_threshold=0.85),
            )
            app.state.provider_router._cache["mock/echo"] = mock_provider

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                # 1. With Persona A
                req1 = {
                    "model": "mock-fast",
                    "messages": [
                        {"role": "system", "content": "You are a pirate."},
                        {"role": "user", "content": "How do I reset my password?"},
                    ],
                    "temperature": 0.0,
                }
                resp1 = await client.post(CHAT_URL, json=req1)
                assert resp1.headers["x-cache"] == "MISS"
                assert mock_provider.complete.call_count == 1

                # 2. Same user question with Persona B -> MUST MISS
                req2 = {
                    "model": "mock-fast",
                    "messages": [
                        {"role": "system", "content": "You are a formal legal assistant."},
                        {"role": "user", "content": "How do I reset my password?"},
                    ],
                    "temperature": 0.0,
                }
                resp2 = await client.post(CHAT_URL, json=req2)
                assert resp2.headers["x-cache"] == "MISS"
                assert mock_provider.complete.call_count == 2

    @pytest.mark.asyncio
    async def test_partition_isolation_multi_turn_different_context_misses(self) -> None:
        """Multi-turn chats with different prior context must not hit semantic cache."""
        app = create_app()
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(
            return_value=InternalResponse(
                response_id="mock-1",
                model="mock-fast",
                content="Context answer.",
                finish_reason="stop",
                provider="mock/echo",
                usage=TokenUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
            )
        )
        mock_provider.supports_streaming = True

        async with app.router.lifespan_context(app):
            app.state.cache_manager = CacheManager(
                exact_store=MemoryExactStore(),
                vector_store=MemoryVectorStore(),
                embedder=MockEmbedder(dim=384),
                settings=Settings(semantic_threshold=0.85),
            )
            app.state.provider_router._cache["mock/echo"] = mock_provider

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                # 1. Chat talking about Python
                req1 = {
                    "model": "mock-fast",
                    "messages": [
                        {"role": "user", "content": "Let's talk about Python."},
                        {"role": "assistant", "content": "Sure! What about Python?"},
                        {"role": "user", "content": "What is the capital?"},
                    ],
                    "temperature": 0.0,
                }
                resp1 = await client.post(CHAT_URL, json=req1)
                assert resp1.headers["x-cache"] == "MISS"
                assert mock_provider.complete.call_count == 1

                # 2. Chat talking about Geography with same final question
                req2 = {
                    "model": "mock-fast",
                    "messages": [
                        {"role": "user", "content": "Let's talk about France."},
                        {"role": "assistant", "content": "Sure! What about France?"},
                        {"role": "user", "content": "What is the capital?"},
                    ],
                    "temperature": 0.0,
                }
                resp2 = await client.post(CHAT_URL, json=req2)
                assert resp2.headers["x-cache"] == "MISS"
                assert mock_provider.complete.call_count == 2
