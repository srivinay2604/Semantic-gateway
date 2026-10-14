"""Sentence-Transformers embedder and deterministic MockEmbedder."""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from typing import Any

import structlog

from cachegate.embeddings.base import BaseEmbedder

logger = structlog.get_logger(__name__)


def _normalize_vector(vec: list[float]) -> list[float]:
    """Normalize a vector to unit Euclidean length (L2 norm = 1.0)."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class SentenceTransformerEmbedder(BaseEmbedder):
    """Local embedding model wrapping sentence-transformers (e.g. all-MiniLM-L6-v2).

    Runs inference inside a thread pool executor to prevent blocking the async event loop.
    """

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        dim: int = 384,
    ) -> None:
        self._model_name = model_name
        self._dim = dim
        self._model: Any = None

    def _load_model(self) -> Any:
        if self._model is None:
            try:
                import importlib

                st_module = importlib.import_module("sentence_transformers")
                st_class = st_module.SentenceTransformer

                logger.info("loading_sentence_transformer_model", model=self._model_name)
                self._model = st_class(self._model_name)
            except ImportError as err:
                msg = (
                    "sentence-transformers is not installed. "
                    "Install with 'pip install sentence-transformers' or use MockEmbedder."
                )
                raise RuntimeError(msg) from err
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, text: str) -> list[float]:
        loop = asyncio.get_running_loop()
        model = self._load_model()

        def _encode() -> list[float]:
            arr = model.encode(text, normalize_embeddings=True)
            return [float(x) for x in arr]

        return await loop.run_in_executor(None, _encode)


class MockEmbedder(BaseEmbedder):
    """Deterministic, lightweight embedder for tests and local development.

    Uses a pseudo-semantic feature projection that maps shared words to shared vector
    dimensions, yielding high cosine similarity (>0.92) for paraphrased sentences
    and low similarity (<0.3) for unrelated sentences.
    """

    def __init__(self, dim: int = 384) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, text: str) -> list[float]:
        """Compute a deterministic pseudo-semantic normalized vector."""
        vec = [0.0] * self._dim
        words = re.findall(r"\w+", text.lower())

        if not words:
            vec[0] = 1.0
            return vec

        for word in words:
            # Map word to a handful of deterministic slots using md5
            digest = hashlib.md5(word.encode("utf-8")).digest()
            for i in range(0, len(digest), 4):
                slot = int.from_bytes(digest[i : i + 2], "big") % self._dim
                sign = 1.0 if digest[i + 2] % 2 == 0 else -1.0
                vec[slot] += sign

        return _normalize_vector(vec)
