"""Base interface for embedding models."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseEmbedder(ABC):
    """Abstract interface for text embedding models."""

    @property
    @abstractmethod
    def dim(self) -> int:
        """Dimension of embedding vectors produced by this model."""
        ...

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Compute normalized float embedding vector for a single string.

        Must run non-blockingly (e.g. in thread pool executor for CPU-bound models).
        """
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Compute embeddings for a batch of strings."""
        return [await self.embed(t) for t in texts]
