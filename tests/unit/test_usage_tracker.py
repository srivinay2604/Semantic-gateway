"""Unit tests for UsageTracker."""

from __future__ import annotations

import pytest

from cachegate.usage.pricing import PricingTable
from cachegate.usage.tracker import UsageTracker


@pytest.mark.asyncio
async def test_usage_tracker_miss_and_hit() -> None:
    pricing = PricingTable("config/pricing.yaml")
    tracker = UsageTracker(pricing_table=pricing)

    # 1. Miss incurs cost
    await tracker.record_request(
        key_id="k1",
        model="gpt-4o-mini",
        prompt_tokens=1000,
        completion_tokens=1000,
        is_hit=False,
    )
    u1 = await tracker.get_usage("k1")
    assert u1["requests"] == 1
    assert u1["hits"] == 0
    assert u1["cost_usd"] > 0.0
    assert u1["cost_saved_usd"] == 0.0

    # 2. Hit incurs 0 cost and records cost saved
    await tracker.record_request(
        key_id="k1",
        model="gpt-4o-mini",
        prompt_tokens=1000,
        completion_tokens=1000,
        is_hit=True,
    )
    u2 = await tracker.get_usage("k1")
    assert u2["requests"] == 2
    assert u2["hits"] == 1
    assert u2["cost_saved_usd"] > 0.0

    # 3. Overall stats
    stats = await tracker.get_all_usage()
    assert stats["total_requests"] == 2
    assert stats["total_hits"] == 1
    assert stats["hit_rate"] == 0.5
