"""OpenAI-compatible embedding provider.

Works with:
- OpenAI (``text-embedding-3-small`` / ``text-embedding-3-large``)
- OpenRouter (any model exposed via ``/v1/embeddings``)
- Ollama (with ``http://localhost:11434/v1``)
- vLLM / LM Studio / NVIDIA NIM / etc.
"""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from tutor.services.embeddings.base import EmbedRequest, EmbedResponse, Embedder


class OpenAICompatEmbedder(Embedder):
    """Provider for OpenAI and OpenAI-compatible embedding APIs."""

    name = "openai_compat"

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
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url,
            default_dimensions=default_dimensions,
            timeout=timeout,
            **kwargs,
        )
        client_kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.api_key:
            client_kwargs["api_key"] = self.api_key
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        # ``OpenAI`` SDK requires api_key; the OpenAI-compatible endpoints
        # that don't need auth (local Ollama) accept any non-empty string.
        if "api_key" not in client_kwargs:
            client_kwargs["api_key"] = "EMPTY"
        self._client = AsyncOpenAI(**client_kwargs)

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        req = self._finalise_request(request)
        inputs = [req.input] if isinstance(req.input, str) else list(req.input)

        params: dict[str, Any] = {"model": req.model, "input": inputs}
        if req.encoding_format and req.encoding_format != "float":
            params["encoding_format"] = req.encoding_format
        if req.dimensions:
            params["dimensions"] = req.dimensions

        resp = await self._client.embeddings.create(**params)
        # ``resp.data`` is a list of objects with ``embedding`` (list[float])
        # and ``index`` — sort by index to guarantee order matches ``inputs``.
        vectors = [list(item.embedding) for item in sorted(resp.data, key=lambda x: x.index)]
        usage: dict[str, int] = {}
        if getattr(resp, "usage", None):
            usage = {
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0,
                "total_tokens": getattr(resp.usage, "total_tokens", 0) or 0,
            }
        return EmbedResponse(
            vectors=vectors,
            model=getattr(resp, "model", req.model),
            usage=usage,
            raw=resp,
        )


__all__ = ["OpenAICompatEmbedder"]
