"""Unit tests for cache key construction and hashing."""

from __future__ import annotations

from cachegate.cache.keys import (
    compute_context_hash,
    compute_exact_request_hash,
    compute_params_hash,
    compute_system_hash,
    exact_cache_key,
    rate_limit_key,
    semantic_cache_key,
    sha256_digest,
    single_flight_lock_key,
    usage_key,
    vector_index_name,
)
from cachegate.schemas.internal import InternalMessage, InternalRequest


def test_sha256_digest() -> None:
    d = sha256_digest("hello")
    assert len(d) == 64
    assert d == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_exact_cache_key_format() -> None:
    key = exact_cache_key("tenant123", "abcdef123456", version="v1")
    assert key == "cg:v1:exact:tenant123:abcdef123456"


def test_semantic_cache_key_format() -> None:
    key = semantic_cache_key("tenant123", "uuid-1234", version="v1")
    assert key == "cg:v1:sem:tenant123:uuid-1234"


def test_other_key_formats() -> None:
    assert vector_index_name("v1") == "idx:cg:v1:sem"
    assert rate_limit_key("key-456") == "cg:v1:rl:key-456"
    assert usage_key("key-456", "20261014") == "cg:v1:usage:key-456:20261014"
    assert single_flight_lock_key("hash789") == "cg:v1:lock:hash789"


def test_partition_hashes() -> None:
    req = InternalRequest(
        request_id="req-1",
        model_alias="gpt-4o-mini",
        resolved_provider="openai/gpt-4o-mini",
        messages=[
            InternalMessage(role="system", content="System Prompt"),
            InternalMessage(role="user", content="Question 1"),
        ],
        temperature=0.1,
    )
    sys_hash = compute_system_hash(req)
    assert sys_hash != "none"
    assert len(sys_hash) == 64

    params_hash = compute_params_hash(req)
    assert len(params_hash) == 64

    # Single-turn has empty context
    ctx_hash = compute_context_hash(req)
    assert ctx_hash == "none"

    exact_hash = compute_exact_request_hash(req)
    assert len(exact_hash) == 64
