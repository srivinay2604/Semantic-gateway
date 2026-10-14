"""Ollama local embedding API adapter."""

from __future__ import annotations

import httpx
import structlog

from cachegate.embeddings.base import BaseEmbedder
from cachegate.embeddings.sbert import _normalize_vector

logger = structlog.get_logger(__name__)


class OllamaEmbedder(BaseEmbedder):
    """Local embedding provider using Ollama's /api/embeddings endpoint."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "nomic-embed-text",
        dim: int = 768,
        timeout_seconds: float = 10.0,
    ) -> None:
        clean_url = base_url.rstrip("/")
        if clean_url.endswith("/v1"):
            clean_url = clean_url[:-3]
        self._base_url = clean_url
        self._model = model
        self._dim = dim
        self._timeout = timeout_seconds

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, text: str) -> list[float]:
        """Request normalized embedding vector from Ollama API."""
        url = f"{self._base_url}/api/embeddings"
        payload = {
            "model": self._model,
            "prompt": text,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.error("ollama_embedding_error", status_code=resp.status_code, body=resp.text)
                resp.raise_for_status()

            data = resp.json()
            embedding = data.get("embedding", [])
            if not embedding:
                msg = "Ollama returned empty embedding"
                raise ValueError(msg)
            self._dim = len(embedding)
            return _normalize_vector([float(x) for x in embedding])
