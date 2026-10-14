"""Unit tests for CacheManager orchestrating lookups, bypasses, semantic hits, and writes."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from cachegate.cache.manager import CacheManager
from cachegate.cache.stores.base import SemanticSearchResult
from cachegate.cache.stores.memory_store import MemoryExactStore
from cachegate.schemas.internal import (
    CacheResult,
    InternalMessage,
    InternalRequest,
    InternalResponse,
    TokenUsage,
)
from cachegate.settings import Settings


def _make_req(
    prompt: str = "What is the capital of France?", temperature: float = 0.0
) -> InternalRequest:
    return InternalRequest(
        request_id="req-test",
        model_alias="mock-fast",
        resolved_provider="mock/echo",
        messages=[InternalMessage(role="user", content=prompt)],
        temperature=temperature,
    )


class TestCacheManager:
    @pytest.mark.asyncio
    async def test_exact_cache_miss_then_hit(self) -> None:
        store = MemoryExactStore()
        manager = CacheManager(exact_store=store)
        req = _make_req()

        # 1. First lookup is a MISS
        resp, status = await manager.get(req)
        assert resp is None
        assert status == "MISS"

        # 2. Put into cache
        provider_resp = InternalResponse(
            response_id="res-1",
            model="mock-fast",
            content="Paris",
            finish_reason="stop",
            provider="mock/echo",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=1, total_tokens=6),
            cache_result=CacheResult.MISS,
        )
        await manager.put(req, provider_resp, ttl_seconds=60)

        # 3. Second lookup is HIT-EXACT
        hit_resp, hit_status = await manager.get(req)
        assert hit_resp is not None
        assert hit_status == "HIT-EXACT"
        assert hit_resp.content == "Paris"
        assert hit_resp.cache_result == CacheResult.HIT_EXACT

    @pytest.mark.asyncio
    async def test_bypass_header_returns_bypass(self) -> None:
        store = MemoryExactStore()
        manager = CacheManager(exact_store=store)
        req = _make_req()

        resp, status = await manager.get(req, headers={"x-cache-bypass": "true"})
        assert resp is None
        assert status == "BYPASS"

    @pytest.mark.asyncio
    async def test_cache_disabled_setting_returns_bypass(self) -> None:
        store = MemoryExactStore()
        custom_settings = Settings(cache_enabled=False)
        manager = CacheManager(exact_store=store, settings=custom_settings)
        req = _make_req()

        resp, status = await manager.get(req)
        assert resp is None
        assert status == "BYPASS"

    @pytest.mark.asyncio
    async def test_semantic_cache_hit_when_exact_misses(self) -> None:
        exact_store = MemoryExactStore()
        mock_vstore = AsyncMock()
        mock_vstore.search = AsyncMock(
            return_value=[
                SemanticSearchResult(
                    entry_id="e-sem",
                    similarity=0.96,
                    content="Paris is the French capital.",
                    provider="mock/echo",
                    usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
                )
            ]
        )
        mock_embedder = AsyncMock()
        mock_embedder.embed = AsyncMock(return_value=[0.1] * 384)

        manager = CacheManager(
            exact_store=exact_store,
            vector_store=mock_vstore,
            embedder=mock_embedder,
        )
        req = _make_req(prompt="Tell me the capital of France")

        resp, status = await manager.get(req)
        assert resp is not None
        assert status == "HIT-SEMANTIC"
        assert resp.content == "Paris is the French capital."
        assert resp.cache_result == CacheResult.HIT_SEMANTIC
        assert resp.cache_similarity == 0.96

    @pytest.mark.asyncio
    async def test_put_stores_both_exact_and_semantic(self) -> None:
        exact_store = MemoryExactStore()
        mock_vstore = AsyncMock()
        mock_vstore.upsert = AsyncMock(return_value=None)
        mock_embedder = AsyncMock()
        mock_embedder.embed = AsyncMock(return_value=[0.1] * 384)

        manager = CacheManager(
            exact_store=exact_store,
            vector_store=mock_vstore,
            embedder=mock_embedder,
        )
        req = _make_req(prompt="What is the capital of France?")
        provider_resp = InternalResponse(
            response_id="res-2",
            model="mock-fast",
            content="Paris",
            finish_reason="stop",
            provider="mock/echo",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=1, total_tokens=6),
            cache_result=CacheResult.MISS,
        )

        await manager.put(req, provider_resp, ttl_seconds=3600)
        assert mock_vstore.upsert.call_count == 1
        assert mock_embedder.embed.call_count == 1

        hit, status = await manager.get(req)
        assert hit is not None
        assert status == "HIT-EXACT"
