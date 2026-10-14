"""OpenAI embedding API adapter."""

from __future__ import annotations

import httpx
import structlog

from cachegate.embeddings.base import BaseEmbedder

logger = structlog.get_logger(__name__)


class OpenAIEmbedder(BaseEmbedder):
    """Remote embedding provider using OpenAI's embedding API."""

    def __init__(
        self,
        api_key: str = "",
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
        dim: int = 1536,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._dim = dim
        self._timeout = timeout_seconds

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, text: str) -> list[float]:
        """Request embedding from OpenAI API."""
        url = f"{self._base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "input": text,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                logger.error("openai_embedding_error", status_code=resp.status_code, body=resp.text)
                resp.raise_for_status()

            data = resp.json()
            embedding = data["data"][0]["embedding"]
            return [float(x) for x in embedding]
