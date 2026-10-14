"""Base interfaces and protocols for exact and semantic cache stores."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from cachegate.schemas.internal import InternalResponse


class ExactCacheStore(ABC):
    """Abstract interface for exact key-value cache storage."""

    @abstractmethod
    async def get(self, key: str) -> InternalResponse | None:
        """Retrieve an entry by key, returning None on miss."""
        ...

    @abstractmethod
    async def set(self, key: str, value: InternalResponse, ttl_seconds: int) -> None:
        """Store an entry with a specified TTL in seconds."""
        ...

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete a key, returning True if removed."""
        ...

    @abstractmethod
    async def flush(self, pattern: str = "*") -> int:
        """Delete keys matching pattern, returning number of deleted keys."""
        ...

    @abstractmethod
    async def ping(self) -> bool:
        """Check store health/connectivity."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close connections cleanly."""
        ...


class SemanticSearchResult:
    """Represents a matched candidate from semantic vector search."""

    def __init__(
        self,
        entry_id: str,
        similarity: float,
        content: str,
        provider: str,
        usage: dict[str, int],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.similarity = similarity
        self.content = content
        self.provider = provider
        self.usage = usage
        self.metadata = metadata or {}


class VectorStore(ABC):
    """Abstract interface for semantic vector storage and KNN search (Phase 5)."""

    @abstractmethod
    async def setup_index(self) -> None:
        """Idempotently create vector index if not exists."""
        ...

    @abstractmethod
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
        """Perform filtered KNN search."""
        ...

    @abstractmethod
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
        """Upsert a vector entry with metadata and TTL."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close vector store resources."""
        ...
