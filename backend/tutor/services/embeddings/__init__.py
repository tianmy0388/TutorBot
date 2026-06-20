"""Embedding provider abstraction.

Embedding providers all expose a single async method ``embed(texts) -> list[list[float]]``
regardless of upstream API (OpenAI, OpenRouter, Ollama, Cohere, ...). The factory
in :mod:`tutor.services.embeddings.embedder_factory` returns the right
implementation based on configuration.
"""

from tutor.services.embeddings.base import Embedder, EmbedRequest, EmbedResponse
from tutor.services.embeddings.embedder_factory import (
    get_runtime_embedder,
    list_embedders,
    register_embedder,
)

__all__ = [
    "Embedder",
    "EmbedRequest",
    "EmbedResponse",
    "get_runtime_embedder",
    "list_embedders",
    "register_embedder",
]
