"""Unit tests for embedder implementations.

Tests MockEmbedder, OpenAIEmbedder, SentenceTransformerEmbedder.
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cachegate.embeddings.openai_embed import OpenAIEmbedder
from cachegate.embeddings.sbert import MockEmbedder, SentenceTransformerEmbedder


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b)


class TestMockEmbedder:
    @pytest.mark.asyncio
    async def test_dim_and_normalization(self) -> None:
        embedder = MockEmbedder(dim=384)
        assert embedder.dim == 384

        vec = await embedder.embed("How do I reset my password?")
        assert len(vec) == 384
        # L2 norm must be approximately 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        assert abs(norm - 1.0) < 1e-4

    @pytest.mark.asyncio
    async def test_paraphrase_similarity_high(self) -> None:
        embedder = MockEmbedder(dim=384)
        v1 = await embedder.embed("How do I reset my account password?")
        v2 = await embedder.embed("How can I reset my account password?")
        sim = _cosine(v1, v2)
        assert sim > 0.85

    @pytest.mark.asyncio
    async def test_unrelated_sentences_similarity_low(self) -> None:
        embedder = MockEmbedder(dim=384)
        v1 = await embedder.embed("How do I reset my account password?")
        v2 = await embedder.embed("Quantum mechanics principles in particle physics.")
        sim = _cosine(v1, v2)
        assert sim < 0.40

    @pytest.mark.asyncio
    async def test_embed_batch(self) -> None:
        embedder = MockEmbedder(dim=384)
        batch = await embedder.embed_batch(["text 1", "text 2"])
        assert len(batch) == 2
        assert len(batch[0]) == 384
        assert len(batch[1]) == 384


class TestOpenAIEmbedder:
    @pytest.mark.asyncio
    async def test_openai_embedder_calls_api(self) -> None:
        embedder = OpenAIEmbedder(api_key="sk-test", dim=1536)
        assert embedder.dim == 1536

        mock_resp = AsyncMock(status_code=200)
        mock_resp.json = MagicMock(return_value={"data": [{"embedding": [0.05] * 1536}]})

        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            vec = await embedder.embed("Hello OpenAI")
            assert len(vec) == 1536
            assert vec[0] == 0.05


class TestSentenceTransformerEmbedder:
    @pytest.mark.asyncio
    async def test_sentence_transformer_missing_package_raises(self) -> None:
        embedder = SentenceTransformerEmbedder(model_name="all-MiniLM-L6-v2")
        with patch.dict("sys.modules", {"sentence_transformers": None}):
            embedder._model = None
            with pytest.raises(RuntimeError) as exc_info:
                await embedder.embed("test")
            assert "sentence-transformers is not installed" in str(exc_info.value)
