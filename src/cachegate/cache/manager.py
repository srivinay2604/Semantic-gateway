"""Placeholder — Phase 4 fills this in.

CacheManager orchestrates the two-tier cache:
  1. Exact lookup (SHA-256 of normalised request).
  2. Semantic lookup (filtered KNN via RediSearch).
On miss, calls the provider and writes to cache in a background task.
"""
# Phase 4+: implement cache manager


def placeholder() -> None:
    """This module is a placeholder for Phase 4."""
