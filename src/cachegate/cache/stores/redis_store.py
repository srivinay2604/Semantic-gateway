"""Redis exact cache and vector store implementation using redis.asyncio."""

from __future__ import annotations

import json
import struct
from typing import Any

import redis.asyncio as aioredis
import structlog
from redis.exceptions import ResponseError

from cachegate.cache.stores.base import ExactCacheStore, SemanticSearchResult, VectorStore
from cachegate.schemas.internal import CacheResult, InternalResponse, InternalUsage

logger = structlog.get_logger(__name__)


def _serialize_response(response: InternalResponse) -> str:
    """Serialize InternalResponse to JSON string."""
    data = {
        "response_id": response.response_id,
        "model": response.model,
        "content": response.content,
        "finish_reason": response.finish_reason,
        "provider": response.provider,
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
        "cache_result": response.cache_result.value,
        "cache_similarity": response.cache_similarity,
    }
    return json.dumps(data)


def _deserialize_response(raw_json: str | bytes, cache_result: CacheResult) -> InternalResponse:
    """Deserialize JSON string into an InternalResponse."""
    if isinstance(raw_json, bytes):
        raw_json = raw_json.decode("utf-8")
    data = json.loads(raw_json)
    usage_data = data.get("usage", {})
    return InternalResponse(
        response_id=data.get("response_id", "cached"),
        model=data.get("model", "unknown"),
        content=data["content"],
        finish_reason=data.get("finish_reason", "stop"),
        provider=data.get("provider", "cached"),
        usage=InternalUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        ),
        cache_result=cache_result,
        cache_similarity=data.get("cache_similarity"),
    )


class RedisExactStore(ExactCacheStore):
    """Exact key-value cache store backed by Redis strings with TTL."""

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
        redis_url: str = "redis://localhost:6379/0",
    ) -> None:
        self._url = redis_url
        self._client: aioredis.Redis = (
            redis_client
            if redis_client is not None
            else aioredis.from_url(redis_url, decode_responses=True)
        )

    async def get(self, key: str) -> InternalResponse | None:
        try:
            val = await self._client.get(key)
            if not val:
                return None
            return _deserialize_response(val, CacheResult.HIT_EXACT)
        except Exception as exc:
            logger.warning("redis_exact_get_error", key=key, error=str(exc))
            return None

    async def set(self, key: str, value: InternalResponse, ttl_seconds: int) -> None:
        try:
            raw = _serialize_response(value)
            await self._client.set(key, raw, ex=max(1, ttl_seconds))
        except Exception as exc:
            logger.warning("redis_exact_set_error", key=key, error=str(exc))

    async def delete(self, key: str) -> bool:
        try:
            deleted = await self._client.delete(key)
            return bool(deleted > 0)
        except Exception as exc:
            logger.warning("redis_exact_delete_error", key=key, error=str(exc))
            return False

    async def flush(self, pattern: str = "*") -> int:
        try:
            keys: list[str] = []
            async for k in self._client.scan_iter(match=pattern, count=100):
                keys.append(k)
            if keys:
                return await self._client.delete(*keys)
            return 0
        except Exception as exc:
            logger.warning("redis_exact_flush_error", pattern=pattern, error=str(exc))
            return 0

    async def ping(self) -> bool:
        try:
            res = await self._client.ping()
            return bool(res)
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()


class RedisVectorStore(VectorStore):
    """RediSearch vector store implementing HNSW indexing and filtered KNN lookup."""

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
        redis_url: str = "redis://localhost:6379/0",
        index_name: str = "idx:cg:v1:sem",
        prefix: str = "cg:v1:sem:",
        dim: int = 384,
    ) -> None:
        self._url = redis_url
        self._index_name = index_name
        self._prefix = prefix
        self._dim = dim
        self._client: aioredis.Redis = (
            redis_client
            if redis_client is not None
            else aioredis.from_url(redis_url, decode_responses=False)
        )

    async def setup_index(self) -> None:
        """Idempotently create RediSearch HNSW vector index."""
        try:
            # Check if index exists
            await self._client.execute_command("FT.INFO", self._index_name)  # type: ignore[no-untyped-call]
            logger.debug("redis_vector_index_exists", index=self._index_name)
        except ResponseError as exc:
            if "unknown index" in str(exc).lower() or "no such index" in str(exc).lower():
                logger.info("creating_redis_vector_index", index=self._index_name, dim=self._dim)
                cmd = [
                    "FT.CREATE",
                    self._index_name,
                    "ON",
                    "HASH",
                    "PREFIX",
                    "1",
                    self._prefix,
                    "SCHEMA",
                    "tenant",
                    "TAG",
                    "model",
                    "TAG",
                    "sys_hash",
                    "TAG",
                    "params_hash",
                    "TAG",
                    "ctx_hash",
                    "TAG",
                    "created_at",
                    "NUMERIC",
                    "embedding",
                    "VECTOR",
                    "HNSW",
                    "6",
                    "TYPE",
                    "FLOAT32",
                    "DIM",
                    str(self._dim),
                    "DISTANCE_METRIC",
                    "COSINE",
                ]
                await self._client.execute_command(*cmd)  # type: ignore[no-untyped-call]
            else:
                logger.warning("redis_index_info_error", error=str(exc))
                raise
        except Exception as exc:
            logger.warning("redis_setup_index_unexpected_error", error=str(exc))
            raise

    async def search(
        self,
        vector: list[float],
        tenant: str,
        model: str,
        sys_hash: str,
        params_hash: str,
        ctx_hash: str,
        top_k: int = 3,
    ) -> list[SemanticSearchResult]:
        """Perform filtered KNN search and convert cosine distance to similarity."""
        try:
            # Escape hyphens and special chars for RediSearch tags
            def _esc(val: str) -> str:
                return val.replace("-", "\\-").replace(":", "\\:").replace(".", "\\.")

            filter_expr = (
                f"(@tenant:{{{_esc(tenant)}}} "
                f"@model:{{{_esc(model)}}} "
                f"@sys_hash:{{{_esc(sys_hash)}}} "
                f"@params_hash:{{{_esc(params_hash)}}} "
                f"@ctx_hash:{{{_esc(ctx_hash)}}})"
            )
            query_str = f"({filter_expr})=>[KNN {top_k} @embedding $vec AS dist]"

            # Pack floats into raw bytes
            vec_bytes = struct.pack(f"{len(vector)}f", *vector)

            cmd = [
                "FT.SEARCH",
                self._index_name,
                query_str,
                "PARAMS",
                "2",
                "vec",
                vec_bytes,
                "SORTBY",
                "dist",
                "DIALECT",
                "2",
            ]
            raw_res = await self._client.execute_command(*cmd)  # type: ignore[no-untyped-call]
            if not raw_res or len(raw_res) <= 1:
                return []

            results: list[SemanticSearchResult] = []
            # raw_res format: [total_count, key1, [field1, val1, ...], key2, ...]
            for i in range(1, len(raw_res), 2):
                key = (
                    raw_res[i].decode("utf-8") if isinstance(raw_res[i], bytes) else str(raw_res[i])
                )
                fields_list = raw_res[i + 1]
                fields_dict: dict[str, Any] = {}
                for j in range(0, len(fields_list), 2):
                    f_name = (
                        fields_list[j].decode("utf-8")
                        if isinstance(fields_list[j], bytes)
                        else str(fields_list[j])
                    )
                    f_val = (
                        fields_list[j + 1].decode("utf-8")
                        if isinstance(fields_list[j + 1], bytes)
                        else fields_list[j + 1]
                    )
                    fields_dict[f_name] = f_val

                dist_str = fields_dict.get("dist", "1.0")
                try:
                    dist = float(dist_str)
                except ValueError:
                    dist = 1.0

                # Cosine distance to similarity: similarity = 1 - distance
                similarity = max(0.0, min(1.0, 1.0 - dist))
                resp_str = fields_dict.get("response", "{}")
                try:
                    parsed_resp = json.loads(resp_str)
                    content = parsed_resp.get("content", "")
                    provider = parsed_resp.get("provider", "cached")
                    usage = parsed_resp.get("usage", {})
                except Exception:
                    content = ""
                    provider = "cached"
                    usage = {}

                results.append(
                    SemanticSearchResult(
                        entry_id=key,
                        similarity=similarity,
                        content=content,
                        provider=provider,
                        usage=usage,
                        metadata={"prompt": fields_dict.get("prompt", "")},
                    )
                )

            return results
        except Exception as exc:
            logger.warning("redis_vector_search_error", error=str(exc))
            return []

    async def upsert(
        self,
        entry_id: str,
        vector: list[float],
        tenant: str,
        model: str,
        sys_hash: str,
        params_hash: str,
        ctx_hash: str,
        response: InternalResponse,
        prompt: str,
        ttl_seconds: int,
    ) -> None:
        """Store semantic entry hash in Redis with TTL."""
        try:
            vec_bytes = struct.pack(f"{len(vector)}f", *vector)
            mapping: dict[str, Any] = {
                "tenant": tenant,
                "model": model,
                "sys_hash": sys_hash,
                "params_hash": params_hash,
                "ctx_hash": ctx_hash,
                "prompt": prompt,
                "response": _serialize_response(response),
                "embedding": vec_bytes,
            }
            await self._client.hset(entry_id, mapping=mapping)  # type: ignore[arg-type]
            if ttl_seconds > 0:
                await self._client.expire(entry_id, ttl_seconds)
        except Exception as exc:
            logger.warning("redis_vector_upsert_error", entry_id=entry_id, error=str(exc))

    async def close(self) -> None:
        await self._client.aclose()
