"""Usage and cost tracking counters following spec §11.1 and §11.4."""

from __future__ import annotations

import datetime
from typing import Any

import redis.asyncio as aioredis
import structlog

from cachegate.cache.keys import usage_key
from cachegate.usage.pricing import PricingTable

logger = structlog.get_logger(__name__)


class UsageTracker:
    """Tracks token usage, requests, cache hits, costs, and cost savings."""

    def __init__(
        self,
        pricing_table: PricingTable | None = None,
        redis_client: aioredis.Redis | None = None,
    ) -> None:
        self.pricing = pricing_table or PricingTable()
        self._client = redis_client
        # In-memory store fallback for tests
        self._memory_counters: dict[str, dict[str, float]] = {}

    def _today_str(self) -> str:
        return datetime.datetime.now(datetime.UTC).strftime("%Y%m%d")

    async def record_request(
        self,
        key_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        is_hit: bool,
    ) -> dict[str, float]:
        """Record usage metrics for an executed request."""
        date_str = self._today_str()
        cost = self.pricing.calculate_cost(model, prompt_tokens, completion_tokens)
        cost_saved = cost if is_hit else 0.0
        incurred_cost = 0.0 if is_hit else cost

        delta = {
            "prompt_tokens": float(prompt_tokens),
            "completion_tokens": float(completion_tokens),
            "cost_usd": incurred_cost,
            "requests": 1.0,
            "hits": 1.0 if is_hit else 0.0,
            "cost_saved_usd": cost_saved,
        }

        # 1. Update in-memory counters
        mem_key = f"{key_id}:{date_str}"
        if mem_key not in self._memory_counters:
            self._memory_counters[mem_key] = {
                "prompt_tokens": 0.0,
                "completion_tokens": 0.0,
                "cost_usd": 0.0,
                "requests": 0.0,
                "hits": 0.0,
                "cost_saved_usd": 0.0,
            }
        for k, v in delta.items():
            self._memory_counters[mem_key][k] += v

        # 2. Update Redis counters if client is present
        if self._client is not None:
            rkey = usage_key(key_id, date_str)
            try:
                pipe = self._client.pipeline()
                pipe.hincrby(rkey, "prompt_tokens", prompt_tokens)
                pipe.hincrby(rkey, "completion_tokens", completion_tokens)
                pipe.hincrbyfloat(rkey, "cost_usd", incurred_cost)
                pipe.hincrby(rkey, "requests", 1)
                if is_hit:
                    pipe.hincrby(rkey, "hits", 1)
                    pipe.hincrbyfloat(rkey, "cost_saved_usd", cost_saved)
                # Expire usage after 90 days
                pipe.expire(rkey, 90 * 86400)
                await pipe.execute()
            except Exception as exc:
                logger.warning("redis_usage_record_failed", key=rkey, error=str(exc))

        return delta

    async def get_usage(self, key_id: str, date_str: str | None = None) -> dict[str, Any]:
        """Retrieve aggregated metrics for a key on a given date (defaults to today)."""
        d_str = date_str or self._today_str()

        # Check Redis first if available
        if self._client is not None:
            rkey = usage_key(key_id, d_str)
            try:
                raw = await self._client.hgetall(rkey)
                if raw:
                    return {
                        "key_id": key_id,
                        "date": d_str,
                        "prompt_tokens": int(raw.get("prompt_tokens", 0)),
                        "completion_tokens": int(raw.get("completion_tokens", 0)),
                        "cost_usd": round(float(raw.get("cost_usd", 0.0)), 6),
                        "requests": int(raw.get("requests", 0)),
                        "hits": int(raw.get("hits", 0)),
                        "cost_saved_usd": round(float(raw.get("cost_saved_usd", 0.0)), 6),
                    }
            except Exception as exc:
                logger.warning("redis_usage_fetch_failed", key=rkey, error=str(exc))

        # Fallback to in-memory counters
        mem_key = f"{key_id}:{d_str}"
        entry = self._memory_counters.get(mem_key, {})
        return {
            "key_id": key_id,
            "date": d_str,
            "prompt_tokens": int(entry.get("prompt_tokens", 0)),
            "completion_tokens": int(entry.get("completion_tokens", 0)),
            "cost_usd": round(float(entry.get("cost_usd", 0.0)), 6),
            "requests": int(entry.get("requests", 0)),
            "hits": int(entry.get("hits", 0)),
            "cost_saved_usd": round(float(entry.get("cost_saved_usd", 0.0)), 6),
        }

    async def get_all_usage(self) -> dict[str, Any]:
        """Aggregate total requests, hits, cost, and savings across all recorded keys."""
        total_requests = 0
        total_hits = 0
        total_cost = 0.0
        total_savings = 0.0
        total_prompt_tokens = 0
        total_completion_tokens = 0

        for record in self._memory_counters.values():
            total_requests += int(record.get("requests", 0))
            total_hits += int(record.get("hits", 0))
            total_cost += float(record.get("cost_usd", 0.0))
            total_savings += float(record.get("cost_saved_usd", 0.0))
            total_prompt_tokens += int(record.get("prompt_tokens", 0))
            total_completion_tokens += int(record.get("completion_tokens", 0))

        hit_rate = (total_hits / total_requests) if total_requests > 0 else 0.0

        return {
            "total_requests": total_requests,
            "total_hits": total_hits,
            "hit_rate": round(hit_rate, 4),
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_cost_usd": round(total_cost, 6),
            "total_saved_usd": round(total_savings, 6),
        }
