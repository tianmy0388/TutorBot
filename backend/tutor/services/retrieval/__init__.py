"""Retrieval service (2026-06-21 plan, Part D).

Exposes :class:`RetrievalService`, :class:`RetrievalScope`, and the
scope parser. The service is a singleton-friendly class — callers
build one and reuse it, or use :func:`get_retrieval_service` for the
process-wide default.
"""

from tutor.services.retrieval.service import (
    DEFAULT_SCORE_THRESHOLD,
    DEFAULT_TOP_K,
    EvidenceChunk,
    RAGContext,
    RetrievalResult,
    RetrievalScope,
    RetrievalService,
    parse_scope,
)


_service: RetrievalService | None = None


def get_retrieval_service() -> RetrievalService:
    """Return the process-wide :class:`RetrievalService` (lazy)."""
    global _service
    if _service is None:
        _service = RetrievalService()
    return _service


def reset_retrieval_service() -> None:
    """Drop the singleton (tests)."""
    global _service
    _service = None


__all__ = [
    "DEFAULT_SCORE_THRESHOLD",
    "DEFAULT_TOP_K",
    "EvidenceChunk",
    "RAGContext",
    "RetrievalResult",
    "RetrievalScope",
    "RetrievalService",
    "get_retrieval_service",
    "parse_scope",
    "reset_retrieval_service",
]
