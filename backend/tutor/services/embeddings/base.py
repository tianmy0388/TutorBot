"""Abstract base + data classes for embedding providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EmbedRequest:
    """A request to an embedding provider.

    ``input`` may be a single string or a list of strings — providers
    typically batch in a single request.
    """

    input: str | list[str]
    model: str = ""
    dimensions: int | None = None  # None = use provider/model default
    encoding_format: str = "float"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class EmbedResponse:
    """Response from an embedding provider.

    ``vectors`` is a list of float lists, one per input item (in order).
    """

    vectors: list[list[float]]
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    raw: Any = None

    @property
    def dim(self) -> int:
        """Return the dimensionality of the returned vectors."""
        return len(self.vectors[0]) if self.vectors else 0


class Embedder(ABC):
    """Abstract base for embedding providers."""

    name: str = "abstract"

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str = "",
        default_dimensions: int = 0,
        timeout: int = 60,
        **kwargs: Any,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.default_dimensions = default_dimensions
        self.timeout = timeout
        self._extra = kwargs

    @abstractmethod
    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        """Produce embeddings for the given input(s)."""
        raise NotImplementedError

    def _finalise_request(self, request: EmbedRequest) -> EmbedRequest:
        if not request.model:
            request.model = self.model
        if request.dimensions is None and self.default_dimensions:
            request.dimensions = self.default_dimensions
        return request


__all__ = ["EmbedRequest", "EmbedResponse", "Embedder"]
