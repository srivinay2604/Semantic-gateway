"""In-memory cache stores for unit testing and local development without Redis."""

from __future__ import annotations

import copy
import fnmatch
import math
import time
from typing import Any

from cachegate.cache.stores.base import ExactCacheStore, SemanticSearchResult, VectorStore
from cachegate.schemas.internal import CacheResult, InternalResponse


class MemoryExactStore(ExactCacheStore):
    """In-memory dictionary exact cache store with monotonic TTL expiration."""

    def __init__(self) -> None:
        # key -> (InternalResponse, expire_at_timestamp)
        self._store: dict[str, tuple[InternalResponse, float]] = {}

    async def get(self, key: str) -> InternalResponse | None:
        entry = self._store.get(key)
        if entry is None:
            return None

        val, expire_at = entry
        if time.monotonic() > expire_at:
            del self._store[key]
            return None

        copied = copy.deepcopy(val)
        copied.cache_result = CacheResult.HIT_EXACT
        return copied

    async def set(self, key: str, value: InternalResponse, ttl_seconds: int) -> None:
        expire_at = time.monotonic() + max(0, ttl_seconds)
        self._store[key] = (copy.deepcopy(value), expire_at)

    async def delete(self, key: str) -> bool:
        return self._store.pop(key, None) is not None

    async def flush(self, pattern: str = "*") -> int:
        keys_to_delete = [k for k in self._store if fnmatch.fnmatch(k, pattern)]
        for k in keys_to_delete:
            del self._store[k]
        return len(keys_to_delete)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self._store.clear()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class MemoryVectorStore(VectorStore):
    """In-memory vector store computing cosine similarity for tests."""

    def __init__(self) -> None:
        # entry_id -> record dict
        self._entries: dict[str, dict[str, Any]] = {}

    async def setup_index(self) -> None:
        """No-op for in-memory store."""
        pass

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
        candidates: list[tuple[float, dict[str, Any]]] = []
        now = time.monotonic()

        for rec in list(self._entries.values()):
            if now > rec["expire_at"]:
                self._entries.pop(rec["entry_id"], None)
                continue

            # Exact partition filter matches
            if rec["tenant"] != tenant:
                continue
            if rec["model"] != model:
                continue
            if rec["sys_hash"] != sys_hash:
                continue
            if rec["params_hash"] != params_hash:
                continue
            if rec["ctx_hash"] != ctx_hash:
                continue

            sim = _cosine_similarity(vector, rec["vector"])
            candidates.append((sim, rec))

        candidates.sort(key=lambda x: x[0], reverse=True)
        results: list[SemanticSearchResult] = []
        for sim, rec in candidates[:top_k]:
            results.append(
                SemanticSearchResult(
                    entry_id=rec["entry_id"],
                    similarity=sim,
                    content=rec["response"].content,
                    provider=rec["response"].provider,
                    usage={
                        "prompt_tokens": rec["response"].usage.prompt_tokens,
                        "completion_tokens": rec["response"].usage.completion_tokens,
                        "total_tokens": rec["response"].usage.total_tokens,
                    },
                    metadata={"prompt": rec["prompt"]},
                )
            )
        return results

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
        self._entries[entry_id] = {
            "entry_id": entry_id,
            "vector": vector,
            "tenant": tenant,
            "model": model,
            "sys_hash": sys_hash,
            "params_hash": params_hash,
            "ctx_hash": ctx_hash,
            "response": copy.deepcopy(response),
            "prompt": prompt,
            "expire_at": time.monotonic() + ttl_seconds,
        }

    async def close(self) -> None:
        self._entries.clear()
