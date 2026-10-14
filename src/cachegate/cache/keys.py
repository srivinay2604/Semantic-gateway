"""Cache key construction and hashing following spec §11.1.

Redis key patterns:
  Exact cache:    cg:{version}:exact:{tenant}:{sha256}
  Semantic entry: cg:{version}:sem:{tenant}:{uuid}
  Vector index:   idx:cg:{version}:sem
  Rate limit:     cg:{version}:rl:{key_id}
  Usage counters: cg:{version}:usage:{key_id}:{yyyymmdd}
  Single flight:  cg:{version}:lock:{sha256}
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cachegate.cache.normalize import (
    canonical_request_json,
    extract_context_messages,
    extract_system_prompt,
    normalize_text,
)
from cachegate.schemas.internal import InternalRequest


def sha256_digest(content: str | bytes) -> str:
    """Compute the hexadecimal SHA-256 digest of input string or bytes."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def compute_exact_request_hash(request: InternalRequest) -> str:
    """Compute deterministic SHA-256 hash of canonical request JSON."""
    canonical_json = canonical_request_json(request)
    return sha256_digest(canonical_json)


def exact_cache_key(tenant: str, request_hash: str, version: str = "v1") -> str:
    """Construct exact cache Redis key: cg:{version}:exact:{tenant}:{sha256}."""
    return f"cg:{version}:exact:{tenant}:{request_hash}"


def semantic_cache_key(tenant: str, entry_id: str, version: str = "v1") -> str:
    """Construct semantic cache entry key: cg:{version}:sem:{tenant}:{entry_id}."""
    return f"cg:{version}:sem:{tenant}:{entry_id}"


def vector_index_name(version: str = "v1") -> str:
    """Construct RediSearch index name: idx:cg:{version}:sem."""
    return f"idx:cg:{version}:sem"


def compute_system_hash(request: InternalRequest) -> str:
    """Compute SHA-256 hash of normalized system prompt or 'none' if absent."""
    sys_prompt = extract_system_prompt(request.messages)
    if not sys_prompt:
        return "none"
    normalized = normalize_text(sys_prompt)
    return sha256_digest(normalized)


def compute_params_hash(request: InternalRequest) -> str:
    """Compute SHA-256 hash of sampling parameters affecting outputs."""
    params: dict[str, Any] = {}
    if request.temperature is not None:
        params["temperature"] = round(request.temperature, 4)
    if request.top_p is not None:
        params["top_p"] = round(request.top_p, 4)
    if request.max_tokens is not None:
        params["max_tokens"] = request.max_tokens
    if request.stop is not None:
        params["stop"] = request.stop

    canonical_params = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return sha256_digest(canonical_params)


def compute_context_hash(request: InternalRequest) -> str:
    """Compute SHA-256 hash of prior conversation context or 'none' if single-turn."""
    context_msgs = extract_context_messages(request.messages)
    if not context_msgs:
        return "none"
    canonical_context = json.dumps(context_msgs, sort_keys=True, separators=(",", ":"))
    return sha256_digest(canonical_context)


def rate_limit_key(key_id: str, version: str = "v1") -> str:
    """Construct rate limit bucket Redis key: cg:{version}:rl:{key_id}."""
    return f"cg:{version}:rl:{key_id}"


def usage_key(key_id: str, yyyymmdd: str, version: str = "v1") -> str:
    """Construct daily usage counters Redis key: cg:{version}:usage:{key_id}:{yyyymmdd}."""
    return f"cg:{version}:usage:{key_id}:{yyyymmdd}"


def single_flight_lock_key(request_hash: str, version: str = "v1") -> str:
    """Construct single-flight Redis lock key: cg:{version}:lock:{sha256}."""
    return f"cg:{version}:lock:{request_hash}"
