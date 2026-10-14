"""Unit tests for exact and vector cache stores.

Tests MemoryExactStore, MemoryVectorStore, RedisExactStore, RedisVectorStore.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ResponseError

from cachegate.cache.stores.memory_store import MemoryExactStore, MemoryVectorStore
from cachegate.cache.stores.redis_store import RedisExactStore, RedisVectorStore
from cachegate.schemas.internal import CacheResult, InternalResponse, TokenUsage


@pytest.fixture
def sample_response() -> InternalResponse:
    return InternalResponse(
        response_id="sample-1",
        model="mock-fast",
        content="Paris is the capital of France.",
        finish_reason="stop",
        provider="mock/echo",
        usage=TokenUsage(prompt_tokens=5, completion_tokens=8, total_tokens=13),
    )


class TestMemoryExactStore:
    @pytest.mark.asyncio
    async def test_set_and_get(self, sample_response: InternalResponse) -> None:
        store = MemoryExactStore()
        await store.set("key1", sample_response, ttl_seconds=10)

        hit = await store.get("key1")
        assert hit is not None
        assert hit.content == sample_response.content
        assert hit.cache_result == CacheResult.HIT_EXACT

        miss = await store.get("nonexistent")
        assert miss is None

    @pytest.mark.asyncio
    async def test_ttl_expiry(self, sample_response: InternalResponse) -> None:
        store = MemoryExactStore()
        await store.set("expiring", sample_response, ttl_seconds=0)
        await asyncio.sleep(0.01)
        hit = await store.get("expiring")
        assert hit is None

    @pytest.mark.asyncio
    async def test_delete_and_flush(self, sample_response: InternalResponse) -> None:
        store = MemoryExactStore()
        await store.set("cg:v1:exact:t1:1", sample_response, ttl_seconds=60)
        await store.set("cg:v1:exact:t1:2", sample_response, ttl_seconds=60)
        await store.set("cg:v1:exact:t2:3", sample_response, ttl_seconds=60)

        deleted = await store.delete("cg:v1:exact:t1:1")
        assert deleted is True

        flushed = await store.flush("cg:v1:exact:t1:*")
        assert flushed == 1

        assert await store.get("cg:v1:exact:t1:2") is None
        assert await store.get("cg:v1:exact:t2:3") is not None
        await store.close()


class TestMemoryVectorStore:
    @pytest.mark.asyncio
    async def test_memory_vector_store_upsert_and_search(
        self, sample_response: InternalResponse
    ) -> None:
        vstore = MemoryVectorStore()
        await vstore.setup_index()

        vector = [0.1] * 384
        await vstore.upsert(
            entry_id="e-1",
            vector=vector,
            tenant="default",
            model="mock-fast",
            sys_hash="sys1",
            params_hash="p1",
            ctx_hash="none",
            response=sample_response,
            prompt="What is Paris?",
            ttl_seconds=60,
        )

        # Search matching partition tags
        hits = await vstore.search(
            vector=vector,
            tenant="default",
            model="mock-fast",
            sys_hash="sys1",
            params_hash="p1",
            ctx_hash="none",
            top_k=3,
        )
        assert len(hits) == 1
        assert hits[0].entry_id == "e-1"
        assert hits[0].similarity > 0.99
        assert hits[0].content == sample_response.content

        # Search with non-matching partition tag returns empty
        miss_hits = await vstore.search(
            vector=vector,
            tenant="different_tenant",
            model="mock-fast",
            sys_hash="sys1",
            params_hash="p1",
            ctx_hash="none",
            top_k=3,
        )
        assert len(miss_hits) == 0
        await vstore.close()


class TestRedisExactStoreMocked:
    @pytest.mark.asyncio
    async def test_redis_store_operations(self, sample_response: InternalResponse) -> None:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=(
                '{"content": "Paris", "finish_reason": "stop", "provider": "mock", '
                '"usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}'
            )
        )
        mock_client.set = AsyncMock(return_value=True)
        mock_client.delete = AsyncMock(return_value=1)
        mock_client.ping = AsyncMock(return_value=True)

        async def _mock_scan_iter(*_args: object, **_kwargs: object) -> AsyncIterator[str]:
            yield "k1"
            yield "k2"

        mock_client.scan_iter = _mock_scan_iter

        store = RedisExactStore(redis_client=mock_client)
        assert await store.ping() is True

        hit = await store.get("some_key")
        assert hit is not None
        assert hit.content == "Paris"
        assert hit.cache_result == CacheResult.HIT_EXACT

        await store.set("some_key", sample_response, ttl_seconds=60)
        assert mock_client.set.call_count == 1

        deleted = await store.delete("some_key")
        assert deleted is True

        flushed = await store.flush("k*")
        assert flushed == 1
        await store.close()

    @pytest.mark.asyncio
    async def test_redis_store_error_handling(self) -> None:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Redis connection refused"))
        mock_client.set = AsyncMock(side_effect=Exception("Redis connection refused"))
        mock_client.delete = AsyncMock(side_effect=Exception("Redis connection refused"))
        mock_client.ping = AsyncMock(side_effect=Exception("Redis connection refused"))

        store = RedisExactStore(redis_client=mock_client)
        assert await store.ping() is False
        assert await store.get("any_key") is None
        assert await store.delete("any_key") is False


class TestRedisVectorStoreMocked:
    @pytest.mark.asyncio
    async def test_setup_index_creation_and_existing(self) -> None:
        mock_client = AsyncMock()
        # Case 1: Index already exists
        mock_client.execute_command = AsyncMock(return_value=True)
        store = RedisVectorStore(redis_client=mock_client, index_name="idx:test")
        await store.setup_index()

        # Case 2: Index unknown -> FT.CREATE called
        mock_client.execute_command = AsyncMock(
            side_effect=[
                ResponseError("Unknown Index name"),
                "OK",
            ]
        )
        await store.setup_index()
        assert mock_client.execute_command.call_count == 2

    @pytest.mark.asyncio
    async def test_vector_search_and_upsert(self, sample_response: InternalResponse) -> None:
        mock_client = AsyncMock()
        resp_data = {
            "content": "Paris",
            "provider": "mock",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        # RediSearch result: [total_count, key1, [f1, v1, ...]]
        search_res = [
            1,
            b"cg:v1:sem:default:e1",
            [
                b"dist",
                b"0.05",
                b"response",
                json.dumps(resp_data).encode("utf-8"),
                b"prompt",
                b"What is Paris?",
            ],
        ]
        mock_client.execute_command = AsyncMock(return_value=search_res)
        mock_client.hset = AsyncMock(return_value=1)
        mock_client.expire = AsyncMock(return_value=True)

        store = RedisVectorStore(redis_client=mock_client)
        results = await store.search(
            vector=[0.1] * 384,
            tenant="default",
            model="mock-fast",
            sys_hash="sys",
            params_hash="params",
            ctx_hash="none",
        )
        assert len(results) == 1
        assert results[0].similarity > 0.9  # 1 - 0.05 = 0.95
        assert results[0].content == "Paris"

        await store.upsert(
            entry_id="e1",
            vector=[0.1] * 384,
            tenant="default",
            model="mock-fast",
            sys_hash="sys",
            params_hash="params",
            ctx_hash="none",
            response=sample_response,
            prompt="What is Paris?",
            ttl_seconds=60,
        )
        assert mock_client.hset.call_count == 1
        assert mock_client.expire.call_count == 1
        await store.close()
