"""Deterministic local embedding provider.

This provider is intentionally small and dependency-free. It gives local
development and seeded courseware a real vector path without requiring a
cloud embedding API key. The vectors are not a replacement for a semantic
embedding model in production, but they are good enough for course-demo RAG,
tests, and offline smoke checks.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable

from tutor.services.embeddings.base import EmbedRequest, EmbedResponse, Embedder


DEFAULT_LOCAL_DIMENSIONS = 384
_LATIN_RE = re.compile(r"[a-z0-9_]+", re.IGNORECASE)


class LocalHashEmbedder(Embedder):
    """A deterministic hashed bag-of-tokens embedder."""

    name = "local_hash"

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        req = self._finalise_request(request)
        inputs = [req.input] if isinstance(req.input, str) else list(req.input)
        dim = int(req.dimensions or self.default_dimensions or DEFAULT_LOCAL_DIMENSIONS)
        vectors = [_embed_text(text, dim) for text in inputs]
        return EmbedResponse(
            vectors=vectors,
            model=req.model or self.model or "local-hash-v1",
            usage={"prompt_tokens": sum(len(str(t)) for t in inputs)},
        )


def _embed_text(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    for token in _tokens(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        weight = 1.0 + min(len(token), 8) * 0.05
        vec[idx] += sign * weight
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def _tokens(text: str) -> Iterable[str]:
    s = (text or "").lower()
    latin_spans: list[tuple[int, int]] = []
    for match in _LATIN_RE.finditer(s):
        word = match.group(0)
        latin_spans.append(match.span())
        yield word
        if len(word) > 4:
            yield word[:4]
            yield word[-4:]

    in_latin = [False] * len(s)
    for start, end in latin_spans:
        for i in range(start, min(end, len(in_latin))):
            in_latin[i] = True

    cjk_chars = [ch for i, ch in enumerate(s) if not in_latin[i] and "\u4e00" <= ch <= "\u9fff"]
    for ch in cjk_chars:
        yield ch
    for n in (2, 3):
        for i in range(0, max(0, len(cjk_chars) - n + 1)):
            yield "".join(cjk_chars[i : i + n])


__all__ = ["DEFAULT_LOCAL_DIMENSIONS", "LocalHashEmbedder"]
