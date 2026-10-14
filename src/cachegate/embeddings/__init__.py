"""Embeddings package."""

from __future__ import annotations

from cachegate.embeddings.base import BaseEmbedder
from cachegate.embeddings.openai_embed import OpenAIEmbedder
from cachegate.embeddings.sbert import MockEmbedder, SentenceTransformerEmbedder

__all__ = [
    "BaseEmbedder",
    "MockEmbedder",
    "OpenAIEmbedder",
    "SentenceTransformerEmbedder",
]
