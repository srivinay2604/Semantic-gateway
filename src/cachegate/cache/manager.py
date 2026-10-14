"""CacheManager orchestrating two-tier caching: exact and semantic."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from typing import Any

import structlog

from cachegate.cache.keys import (
    compute_context_hash,
    compute_exact_request_hash,
    compute_params_hash,
    compute_system_hash,
    exact_cache_key,
    semantic_cache_key,
)
from cachegate.cache.normalize import extract_last_user_message
from cachegate.cache.policy import is_request_cacheable, is_response_cacheable
from cachegate.cache.stores.base import ExactCacheStore, VectorStore
from cachegate.observability.metrics import (
    cache_errors_total,
    cache_hits_total,
    cache_lookup_duration_seconds,
    cache_similarity,
    embedding_duration_seconds,
)
from cachegate.schemas.internal import CacheResult, InternalRequest, InternalResponse, InternalUsage
from cachegate.settings import Settings
from cachegate.settings import settings as default_settings

logger = structlog.get_logger(__name__)


class CacheManager:
    """Coordinates exact cache and semantic vector cache lookups and writes."""

    def __init__(
        self,
        exact_store: ExactCacheStore,
        vector_store: VectorStore | None = None,
        embedder: Any | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.exact_store = exact_store
        self.vector_store = vector_store
        self.embedder = embedder
        self.settings = settings or default_settings

    async def get(
        self,
        request: InternalRequest,
        headers: Mapping[str, str] | None = None,
        tenant: str = "default",
    ) -> tuple[InternalResponse | None, str]:
        """Perform exact lookup (Tier 1) and semantic lookup (Tier 2).

        Returns:
            Tuple of (response_or_none, cache_header_value).
            Cache header value is one of: "HIT-EXACT", "HIT-SEMANTIC", "MISS", "BYPASS".
        """
        if not self.settings.cache_enabled:
            return None, "BYPASS"

        # Check request cacheability
        cacheable, reason = is_request_cacheable(
            request,
            headers=headers,
            max_temperature=self.settings.cache_max_temperature,
        )
        if not cacheable:
            logger.debug(
                "cache_request_skipped",
                model=request.model_alias,
                reason=reason,
            )
            # If explicit bypass requested via header, emit BYPASS
            if reason in ("cache_control_no_cache", "x_cache_bypass"):
                return None, "BYPASS"
            return None, "MISS"

        # ---------------------------------------------------------------------
        # Tier 1: Exact Cache Lookup
        # ---------------------------------------------------------------------
        request_hash = compute_exact_request_hash(request)
        key = exact_cache_key(
            tenant=tenant,
            request_hash=request_hash,
            version=self.settings.cache_schema_version,
        )

        t_exact = time.monotonic()
        try:
            exact_hit = await self.exact_store.get(key)
            cache_lookup_duration_seconds.labels(tier="exact").observe(time.monotonic() - t_exact)
            if exact_hit is not None:
                exact_hit.cache_result = CacheResult.HIT_EXACT
                cache_hits_total.labels(tier="exact", tenant=tenant).inc()
                logger.info(
                    "exact_cache_hit",
                    model=request.model_alias,
                    tenant=tenant,
                    key=key,
                )
                return exact_hit, "HIT-EXACT"
        except Exception as exc:
            cache_errors_total.labels(operation="exact_get").inc()
            logger.warning("exact_cache_lookup_failed", error=str(exc))

        # ---------------------------------------------------------------------
        # Tier 2: Semantic Cache Lookup (Phase 5)
        # ---------------------------------------------------------------------
        if self.vector_store is not None and self.embedder is not None:
            semantic_hit = await self._search_semantic(
                request=request,
                headers=headers,
                tenant=tenant,
            )
            if semantic_hit is not None:
                return semantic_hit, "HIT-SEMANTIC"

        return None, "MISS"

    async def _search_semantic(
        self,
        request: InternalRequest,
        headers: Mapping[str, str] | None,
        tenant: str,
    ) -> InternalResponse | None:
        """Internal helper for semantic vector lookup."""
        if self.vector_store is None or self.embedder is None:
            return None

        last_user_msg = extract_last_user_message(request.messages)
        if not last_user_msg:
            return None

        # Parse threshold from header if present, else fallback to settings
        threshold = self.settings.semantic_threshold
        if headers:
            header_th = headers.get("x-cache-threshold")
            if header_th:
                try:
                    threshold = max(0.0, min(1.0, float(header_th)))
                except ValueError:
                    pass

        t_sem = time.monotonic()
        try:
            # Generate embedding
            t_emb = time.monotonic()
            query_vector = await self.embedder.embed(last_user_msg)
            embedding_duration_seconds.observe(time.monotonic() - t_emb)

            sys_hash = compute_system_hash(request)
            params_hash = compute_params_hash(request)
            ctx_hash = compute_context_hash(request)

            results = await self.vector_store.search(
                vector=query_vector,
                tenant=tenant,
                model=request.model_alias,
                sys_hash=sys_hash,
                params_hash=params_hash,
                ctx_hash=ctx_hash,
                top_k=self.settings.semantic_top_k,
            )
            cache_lookup_duration_seconds.labels(tier="semantic").observe(time.monotonic() - t_sem)

            if results and results[0].similarity >= threshold:
                top = results[0]
                cache_hits_total.labels(tier="semantic", tenant=tenant).inc()
                cache_similarity.observe(round(top.similarity, 4))
                logger.info(
                    "semantic_cache_hit",
                    model=request.model_alias,
                    similarity=top.similarity,
                    threshold=threshold,
                    entry_id=top.entry_id,
                )
                return InternalResponse(
                    response_id=f"sem-{top.entry_id}",
                    model=request.model_alias,
                    content=top.content,
                    finish_reason="stop",
                    provider=top.provider,
                    usage=InternalUsage(
                        prompt_tokens=top.usage.get("prompt_tokens", 0),
                        completion_tokens=top.usage.get("completion_tokens", 0),
                        total_tokens=top.usage.get("total_tokens", 0),
                    ),
                    cache_result=CacheResult.HIT_SEMANTIC,
                    cache_similarity=round(top.similarity, 4),
                )
        except Exception as exc:
            cache_errors_total.labels(operation="semantic_search").inc()
            logger.warning("semantic_cache_lookup_failed", error=str(exc))

        return None

    async def put(
        self,
        request: InternalRequest,
        response: InternalResponse,
        tenant: str = "default",
        ttl_seconds: int | None = None,
    ) -> None:
        """Write a successful response to exact and semantic caches."""
        if not self.settings.cache_enabled:
            return

        cacheable, reason = is_response_cacheable(response)
        if not cacheable:
            logger.debug(
                "cache_response_skipped",
                model=request.model_alias,
                reason=reason,
            )
            return

        ttl = ttl_seconds if ttl_seconds is not None else self.settings.cache_ttl_seconds

        # 1. Exact Cache Write
        try:
            request_hash = compute_exact_request_hash(request)
            exact_key = exact_cache_key(
                tenant=tenant,
                request_hash=request_hash,
                version=self.settings.cache_schema_version,
            )
            await self.exact_store.set(exact_key, response, ttl)
            logger.debug("exact_cache_stored", key=exact_key, ttl=ttl)
        except Exception as exc:
            cache_errors_total.labels(operation="exact_set").inc()
            logger.warning("exact_cache_write_failed", error=str(exc))

        # 2. Semantic Cache Write (Phase 5)
        if self.vector_store is not None and self.embedder is not None:
            last_user_msg = extract_last_user_message(request.messages)
            if last_user_msg:
                try:
                    entry_id = semantic_cache_key(
                        tenant=tenant,
                        entry_id=str(uuid.uuid4()),
                        version=self.settings.cache_schema_version,
                    )
                    vector = await self.embedder.embed(last_user_msg)
                    sys_hash = compute_system_hash(request)
                    params_hash = compute_params_hash(request)
                    ctx_hash = compute_context_hash(request)

                    await self.vector_store.upsert(
                        entry_id=entry_id,
                        vector=vector,
                        tenant=tenant,
                        model=request.model_alias,
                        sys_hash=sys_hash,
                        params_hash=params_hash,
                        ctx_hash=ctx_hash,
                        response=response,
                        prompt=last_user_msg,
                        ttl_seconds=ttl,
                    )
                    logger.debug("semantic_cache_stored", entry_id=entry_id)
                except Exception as exc:
                    cache_errors_total.labels(operation="semantic_upsert").inc()
                    logger.warning("semantic_cache_write_failed", error=str(exc))
