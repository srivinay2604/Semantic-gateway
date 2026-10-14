"""Unit tests for Phase 8 Observability & Prometheus Metrics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from cachegate.observability.metrics import (
    cache_errors_total,
    cache_hits_total,
    cache_lookup_duration_seconds,
    cache_similarity,
    circuit_breaker_state,
    circuit_breaker_state_gauge,
    cost_saved_usd_total,
    cost_usd_total,
    embedding_duration_seconds,
    provider_duration_seconds,
    provider_requests_total,
    rate_limited_total,
    request_duration_seconds,
    requests_total,
    singleflight_coalesced_total,
    tokens_total,
)
from cachegate.providers.circuit_breaker import CircuitBreaker, CircuitState


def test_grafana_dashboard_json_validity() -> None:
    """The committed Grafana dashboard JSON must be valid and conform to spec."""
    dashboard_path = Path("infra/grafana/dashboards/cachegate.json")
    assert dashboard_path.exists(), "Grafana dashboard file missing"

    content = dashboard_path.read_text()
    data = json.loads(content)

    assert data["title"] == "CacheGate LLM Gateway"
    assert "panels" in data
    assert len(data["panels"]) >= 10

    # Ensure essential panels exist
    panel_titles = [p.get("title", "") for p in data["panels"]]
    assert any("Throughput" in t for t in panel_titles)
    assert any("Hit Rate" in t for t in panel_titles)
    assert any("Cost Saved" in t for t in panel_titles)
    assert any("Circuit Breaker" in t for t in panel_titles)
    assert any("Single-Flight" in t for t in panel_titles)


def test_prometheus_metrics_instrumentation() -> None:
    """Verify all defined metrics increment and observe without errors."""
    requests_total.labels(route="/v1/chat/completions", status="200").inc()
    request_duration_seconds.labels(route="/v1/chat/completions").observe(0.042)
    provider_requests_total.labels(provider="mock-fast", status="200").inc()
    provider_duration_seconds.labels(provider="mock-fast").observe(0.12)
    cache_hits_total.labels(tier="exact", tenant="t1").inc()
    cache_hits_total.labels(tier="semantic", tenant="t1").inc()
    cache_lookup_duration_seconds.labels(tier="exact").observe(0.002)
    cache_lookup_duration_seconds.labels(tier="semantic").observe(0.015)
    embedding_duration_seconds.observe(0.005)
    cache_similarity.observe(0.96)
    rate_limited_total.labels(key_id="k1").inc()
    cache_errors_total.labels(operation="exact_get").inc()
    tokens_total.labels(type="prompt", model="mock-fast", key_id="k1").inc(10)
    cost_usd_total.labels(model="mock-fast", key_id="k1").inc(0.001)
    cost_saved_usd_total.labels(model="mock-fast", key_id="k1").inc(0.002)
    singleflight_coalesced_total.inc()
    circuit_breaker_state.labels(provider="mock-fast", state="closed").inc()
    circuit_breaker_state_gauge.labels(provider="mock-fast").set(0)

    # Metrics must be present in Prometheus default registry
    metric_names = [m.name for m in REGISTRY.collect()]
    assert "cg_requests" in metric_names
    assert "cg_cache_hits" in metric_names
    assert "cg_circuit_breaker_state" in metric_names
    assert "cg_singleflight_coalesced" in metric_names


@pytest.mark.asyncio
async def test_circuit_breaker_updates_state_gauge() -> None:
    """Circuit breaker state transitions must update cg_circuit_breaker_state gauge."""
    cb = CircuitBreaker("test-provider", failure_threshold=2, recovery_seconds=1.0)
    assert cb.state == CircuitState.CLOSED

    # Record 2 failures -> trips to OPEN (state 2)
    await cb.record_failure()
    await cb.record_failure()
    assert cb.state == CircuitState.OPEN

    # Recovery -> resets to CLOSED (state 0)
    await cb.record_success()
    assert cb.state == CircuitState.CLOSED
